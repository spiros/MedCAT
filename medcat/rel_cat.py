import json
import logging
import os
from tokenize import Token

import torch.nn
import torch.optim
import torch
import torch.nn as nn

from tqdm import tqdm
from datetime import date, datetime
from transformers import BertConfig
from medcat.cdb import CDB
from medcat.config import Config
from medcat.config_rel_cat import ConfigRelCAT
from medcat.pipeline.pipe_runner import PipeRunner
from medcat.utils.loggers import add_handlers
from medcat.utils.relation_extraction.tokenizer import TokenizerWrapperBERT
from spacy.tokens import Doc
from typing import Iterable, Iterator, cast
from transformers import AutoTokenizer
from torch.utils.data import DataLoader
from medcat.utils.meta_cat.ml_utils import split_list_train_test
from medcat.utils.meta_cat.ml_utils import set_all_seeds, split_list_train_test

from medcat.utils.relation_extraction.models import BertModel_RelationExtraction
from medcat.utils.relation_extraction.pad_seq import Pad_Sequence

from medcat.utils.relation_extraction.utils import create_tokenizer_pretrain,  load_results, load_state, save_results, save_state

from medcat.utils.relation_extraction.rel_dataset import RelData

class RelCAT(PipeRunner):


    name = "rel_cat"
    
    log = logging.getLogger(__package__)
    # Add file and console handlers
    log = add_handlers(log)

    def __init__(self, cdb: CDB, tokenizer: TokenizerWrapperBERT, config: ConfigRelCAT = ConfigRelCAT(), task="train"):
        self.config = config
        self.tokenizer : TokenizerWrapperBERT = tokenizer
        self.cdb = cdb

        self.log.setLevel(self.config.general['log_level'])

        self.learning_rate = config.train["lr"]
        self.batch_size = config.train["batch_size"]
        self.nclasses = config.model["nclasses"]

        self.is_cuda_available = torch.cuda.is_available()
        self.device = torch.device("cuda" if self.is_cuda_available and self.config.general["device"] != "cpu" else "cpu")
        self.model_config = BertConfig()
        self.model: BertModel_RelationExtraction
        self.task = task
        self.checkpoint_path = "./"
        self.optimizer = None
        self.scheduler = None
        self.best_f1 = 0.0
        self.epoch = 0

        self.pad_id = self.tokenizer.hf_tokenizers.pad_token_id
        self.padding_seq = Pad_Sequence(seq_pad_value=self.pad_id,
                       label_pad_value=self.pad_id,
                       label2_pad_value=-1)

        set_all_seeds(config.general['seed'])

    def save(self, save_path) -> None:
        self.config.save(os.path.join(save_path, "config.json"))
        self.model_config.to_json_file(os.path.join(save_path, "model_config.json"))
        self.tokenizer.save(os.path.join(save_path, self.config.general["tokenizer_name"]))

        save_state(self.model, self.optimizer, self.scheduler, self.epoch, self.best_f1,
                    save_path, self.config.general["model_name"],
                    self.task, is_checkpoint=False
                )

    @classmethod
    def load(cls, load_path: str = "./") -> "RelCAT":

        cdb = CDB(config=Config())
        if os.path.exists(os.path.join(load_path, "cdb.dat")):
            cdb = CDB.load(os.path.join(load_path, "cdb.dat"))
        else:
            print("The default CDB file name 'cdb.dat' doesn't exist in the specified path, you will need to load & set \
                          a CDB manually via rel_cat.cdb = CDB.load('path') ")

        config_path = os.path.join(load_path, "config.json")
        config = ConfigRelCAT()
        if os.path.exists(config_path):
            config = cast(ConfigRelCAT, ConfigRelCAT.load(os.path.join(load_path, "config.json")))

        tokenizer = None
        tokenizer_path = os.path.join(load_path, config.general["tokenizer_name"])

        if os.path.exists(tokenizer_path):
            tokenizer = TokenizerWrapperBERT.load(tokenizer_path)
        elif config.general["model_name"]:  
            tokenizer = TokenizerWrapperBERT(AutoTokenizer.from_pretrained(pretrained_model_name_or_path=config.general["model_name"]), 
                                            max_seq_length=config.general["max_seq_length"],
                                            add_special_tokens=config.general["tokenizer_special_tokens"]
                                            )
            create_tokenizer_pretrain(tokenizer, tokenizer_path)
        else:
            tokenizer = TokenizerWrapperBERT(AutoTokenizer.from_pretrained(pretrained_model_name_or_path="bert-base-uncased"),
                                            max_seq_length=config.general["max_seq_length"],
                                            add_special_tokens=config.general["tokenizer_special_tokens"]
                                            )

        model_config = BertConfig()
        model_config_path = os.path.join(load_path, "model_config.json")

        if os.path.exists(model_config_path):
            print("Loaded config from : ", model_config_path)
            model_config = BertConfig.from_json_file(model_config_path) # type: ignore
        else:
            try:
                model_config = BertConfig.from_pretrained(pretrained_model_name_or_path=config.general["model_name"]) # type: ignore
            except Exception as e:
                logging.error("%s", str(e))
                print("Config for HF model not found: ", config.general["model_name"], ". Using bert-base-uncased.")
                model_config = BertConfig.from_pretrained(pretrained_model_name_or_path="bert-base-uncased") # type: ignore

        model_config.vocab_size = len(tokenizer.hf_tokenizers)

        rel_cat = cls(cdb=cdb, config=config, tokenizer=tokenizer, task=config.general["task"])
        rel_cat.model_config = model_config

        device = torch.device("cuda" if torch.cuda.is_available() and config.general["device"] != "cpu" else "cpu")

        try:
            rel_cat.model = BertModel_RelationExtraction.from_pretrained(pretrained_model_name_or_path=config.general["model_name"],
                                                                       model_size=config.model["hidden_size"],
                                                                       relcat_config=config,
                                                                       model_config=model_config,
                                                                       task=config.general["task"],
                                                                       nclasses=config.model["nclasses"],
                                                                       ignore_mismatched_sizes=True) 
            print("Loaded HF model : ", config.general["model_name"])
        except Exception as e:
            logging.error("%s", str(e))
            print("Failed to load specified HF model, defaulting to 'bert-base-uncased', loading...")
            rel_cat.model = BertModel_RelationExtraction.from_pretrained(pretrained_model_name_or_path="bert-base-uncased",
                                                                        model_size=config.model["hidden_size"],
                                                                        relcat_config=config,
                                                                        model_config=model_config,
                                                                        task=config.general["task"],
                                                                        nclasses=config.model["nclasses"],
                                                                        ignore_mismatched_sizes=True) 

        rel_cat.model = torch.nn.DataParallel(rel_cat.model) # type: ignore
        rel_cat.model = rel_cat.model.to(device) # type: ignore

        rel_cat.optimizer = None
        rel_cat.scheduler = None

        rel_cat.epoch, rel_cat.best_f1 = load_state(rel_cat.model, rel_cat.optimizer, rel_cat.scheduler, path=load_path,
                                                    model_name=config.general["model_name"],
                                                    file_prefix=config.general["task"],
                                                    device=device,
                                                    config=config)

        return rel_cat

    def create_test_train_datasets(self, data, split_sets=False):
        train_data, test_data = {}, {}

        if split_sets:
            train_data["output_relations"], test_data["output_relations"] = split_list_train_test(data["output_relations"],
                            test_size=self.config.train["test_size"], shuffle=False)


            test_data_label_names = [rec[4] for rec in test_data["output_relations"]]
            test_data["nclasses"], test_data["labels2idx"], test_data["idx2label"] = RelData.get_labels(test_data_label_names, self.config)

            for idx in range(len(test_data["output_relations"])):
                test_data["output_relations"][idx][5] = test_data["labels2idx"][test_data["output_relations"][idx][4]]
        else:
            train_data["output_relations"] = data["output_relations"]

        for k, v in data.items():
            if k != "output_relations":
                train_data[k] = []
                test_data[k] = []

        train_data_label_names = [rec[4] for rec in train_data["output_relations"]]
        train_data["nclasses"], train_data["labels2idx"], train_data["idx2label"] = RelData.get_labels(train_data_label_names, self.config)

        for idx in range(len(train_data["output_relations"])):
            train_data["output_relations"][idx][5] = train_data["labels2idx"][train_data["output_relations"][idx][4]]

        return train_data, test_data

    def train(self, export_data_path="", train_csv_path="", test_csv_path="", checkpoint_path="./"):

        if self.is_cuda_available:
            print("Training on device:", torch.cuda.get_device_name(0), self.device)

        self.model = self.model.to(self.device)

        # resize vocab just in case more tokens have been added
        self.model_config.vocab_size = len(self.tokenizer.hf_tokenizers)

        train_rel_data = RelData(cdb=self.cdb, config=self.config, tokenizer=self.tokenizer)
        test_rel_data = RelData(cdb=self.cdb, config=self.config, tokenizer=self.tokenizer)

        if train_csv_path != "":
            if test_csv_path != "":
                train_rel_data.dataset, _ = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(train_csv_path), split_sets=False)
                test_rel_data.dataset, _ = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(test_csv_path), split_sets=False)
            else:
                train_rel_data.dataset, test_rel_data.dataset = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(train_csv_path), split_sets=True)

        elif export_data_path != "":
            export_data = {}
            with open(export_data_path) as f:
                export_data = json.load(f)
            train_rel_data.dataset, test_rel_data.dataset = self.create_test_train_datasets(train_rel_data.create_relations_from_export(export_data), split_sets=True)
        else:
            raise ValueError("NO DATA HAS BEEN PROVIDED (JSON/CSV/spacy_DOCS)")


        train_dataset_size = len(train_rel_data)
        batch_size = train_dataset_size if train_dataset_size < self.batch_size else self.batch_size
        train_dataloader = DataLoader(train_rel_data, batch_size=batch_size, shuffle=self.config.train["shuffle_data"],
                                  num_workers=0, collate_fn=self.padding_seq, pin_memory=self.config.general["pin_memory"])

        test_dataset_size = len(test_rel_data)
        test_batch_size = test_dataset_size if test_dataset_size < self.batch_size else self.batch_size
        test_dataloader = DataLoader(test_rel_data, batch_size=test_batch_size, shuffle=self.config.train["shuffle_data"],
                                 num_workers=0, collate_fn=self.padding_seq, pin_memory=self.config.general["pin_memory"])

        criterion = nn.CrossEntropyLoss(ignore_index=-1)    

        if self.optimizer is None:
            self.optimizer = torch.optim.Adam([{"params": self.model.bert_model.parameters(), "lr": self.config.train["adam_epsilon"]}])

        if self.scheduler is None:
            self.scheduler = torch.optim.lr_scheduler.MultiStepLR(self.optimizer, milestones=self.config.train["multistep_milestones"], gamma=self.config.train["multistep_lr_gamma"])

        self.epoch, self.best_f1 = load_state(self.model, self.optimizer, self.scheduler, load_best=False, path=checkpoint_path,device=self.device) 

        self.log.info("Starting training process...")

        losses_per_epoch, accuracy_per_epoch, f1_per_epoch = load_results(path=checkpoint_path)

        if train_rel_data.dataset["nclasses"] > self.model.nclasses:
            self.model.nclasses = self.config.model["nclasses"] = train_rel_data.dataset["nclasses"]

        self.config.general["labels2idx"].update(train_rel_data.dataset["labels2idx"])
        self.config.general["idx2labels"] = {int(v): k for k, v in self.config.general["labels2idx"].items()}

        gradient_acc_steps = self.config.train["gradient_acc_steps"]
        max_grad_norm = self.config.train["max_grad_norm"]

        _epochs = self.epoch + self.config.train["nepochs"]

        for epoch in range(0, _epochs):
            start_time = datetime.now().time()
            total_loss = 0.0

            loss_per_batch = []
            accuracy_per_batch = []

            self.log.info("Total epochs on this model: %d | currently training epoch %d" % (_epochs, epoch))

            pbar = tqdm(total=train_dataset_size)

            for i, data in enumerate(train_dataloader, 0): 
                self.model.train()
                self.model.zero_grad()

                current_batch_size = len(data[0])
                token_ids, e1_e2_start, labels, _, _, _ = data

                attention_mask = (token_ids != self.pad_id).float().to(self.device)
                token_type_ids = torch.zeros((token_ids.shape[0], token_ids.shape[1])).long().to(self.device)
                labels = labels.to(self.device)

                model_output, classification_logits = self.model(
                            input_ids=token_ids,
                            token_type_ids=token_type_ids,
                            attention_mask=attention_mask,
                            e1_e2_start=e1_e2_start
                          )

                batch_loss = criterion(classification_logits.view(-1, self.model.nclasses).to(self.device), labels.squeeze(1))
                batch_loss = batch_loss / gradient_acc_steps

                total_loss += batch_loss.item() / current_batch_size

                batch_loss.backward()

                batch_acc, _, batch_precision, batch_f1, _, _, batch_stats_per_label = self.evaluate_(classification_logits, labels, ignore_idx=-1)               

                loss_per_batch.append(batch_loss / current_batch_size)
                accuracy_per_batch.append(batch_acc)

                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_grad_norm)

                if (i % gradient_acc_steps) == 0:
                    self.optimizer.step()
                    self.scheduler.step()

                if ((i + 1) % current_batch_size == 0):
                    self.log.debug("[Epoch: %d, loss per batch, accuracy per batch: %.3f, %.3f, average total loss %.3f , total loss %.3f]" %
                        (epoch, loss_per_batch[-1], accuracy_per_batch[-1], total_loss / (i + 1), total_loss))

                pbar.update(current_batch_size)

            pbar.close()

            if len(loss_per_batch) > 0:
                losses_per_epoch.append(sum(loss_per_batch)/len(loss_per_batch))
                self.log.info("Losses at Epoch %d: %.5f" % (epoch, losses_per_epoch[-1]))

            if len(accuracy_per_batch) > 0:
                accuracy_per_epoch.append(sum(accuracy_per_batch)/len(accuracy_per_batch))
                self.log.info("Train accuracy at Epoch %d: %.5f" % (epoch, accuracy_per_epoch[-1]))

            total_loss = total_loss / (i + 1)

            end_time = datetime.now().time()

            results = self.evaluate_results(test_dataloader, self.pad_id)

            f1_per_epoch.append(results['f1'])

            self.log.info("Epoch finished, took " + str(datetime.combine(date.today(), end_time) - datetime.combine(date.today(), start_time)) + " seconds")

            self.epoch += 1

            if len(f1_per_epoch) > 0 and f1_per_epoch[-1] > self.best_f1:
                self.best_f1 = f1_per_epoch[-1]
                save_state(self.model, self.optimizer, self.scheduler, self.epoch, self.best_f1, checkpoint_path,
                            model_name=self.config.general["model_name"], task=self.task, is_checkpoint=False)

            if (epoch % 1) == 0:
                save_results({"losses_per_epoch": losses_per_epoch, "accuracy_per_epoch": accuracy_per_epoch, "f1_per_epoch": f1_per_epoch, "epoch": epoch}, file_prefix="train", path=checkpoint_path)
                save_state(self.model, self.optimizer, self.scheduler, self.epoch, self.best_f1, checkpoint_path,
                            model_name=self.config.general["model_name"], task=self.task)

    def evaluate_(self, output_logits, labels, ignore_idx):
        # ignore index (padding) when calculating accuracy
        idxs = (labels != ignore_idx).squeeze()
        labels_ = labels.squeeze()[idxs].to(self.device)
        pred_labels = torch.softmax(output_logits, dim=1).max(1)[1]
        pred_labels = pred_labels[idxs].to(self.device)

        true_labels = labels_.cpu().numpy().tolist() if labels_.is_cuda else labels_.numpy().tolist()
        pred_labels = pred_labels.cpu().numpy().tolist() if pred_labels.is_cuda else pred_labels.numpy().tolist()

        unique_labels = set(true_labels)

        batch_size = len(true_labels)

        stat_per_label = dict()

        total_tp, total_fp, total_tn, total_fn = 0, 0, 0, 0
        acc, micro_recall, micro_precision, micro_f1 = 0, 0, 0, 0

        for label in unique_labels:
            stat_per_label[label] = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "f1": 0.0, "acc": 0.0, "prec": 0.0, "recall": 0.0}
            for true_label_idx in range(len(true_labels)):
                if true_labels[true_label_idx] == label:
                    if pred_labels[true_label_idx] == label:
                        stat_per_label[label]["tp"] += 1
                        total_tp += 1 
                    if pred_labels[true_label_idx] != label:
                        stat_per_label[label]["fp"] += 1
                        total_fp += 1 
                elif true_labels[true_label_idx] != label and label == pred_labels[true_label_idx]:
                    stat_per_label[label]["fn"] += 1
                    total_fn += 1 
                else:
                    stat_per_label[label]["tn"] += 1
                    total_tn += 1 

            lbl_tp_tn = stat_per_label[label]["tn"] + stat_per_label[label]["tp"]

            lbl_tp_fn = stat_per_label[label]["fn"] + stat_per_label[label]["tp"]   
            lbl_tp_fn = lbl_tp_fn if lbl_tp_fn > 0.0 else 1.0 

            lbl_tp_fp = stat_per_label[label]["tp"] + stat_per_label[label]["fp"] 
            lbl_tp_fp = lbl_tp_fp if lbl_tp_fp > 0.0 else 1.0 

            stat_per_label[label]["acc"] = lbl_tp_tn / batch_size
            stat_per_label[label]["prec"] = stat_per_label[label]["tp"] / lbl_tp_fp
            stat_per_label[label]["recall"] = stat_per_label[label]["tp"]  / lbl_tp_fn

            lbl_re_pr = stat_per_label[label]["recall"] + stat_per_label[label]["prec"] 
            lbl_re_pr = lbl_re_pr if lbl_re_pr > 0.0 else 1.0   

            stat_per_label[label]["f1"] = (2 * (stat_per_label[label]["recall"] * stat_per_label[label]["prec"])) / lbl_re_pr

        tp_fn = total_fn + total_tp
        tp_fn = tp_fn if tp_fn > 0.0 else 1.0

        tp_fp = total_fp + total_tp
        tp_fp = tp_fp if tp_fp > 0.0 else 1.0 

        micro_recall = total_tp / tp_fn
        micro_precision = total_tp / tp_fp

        re_pr = micro_recall + micro_precision 
        re_pr = re_pr if re_pr > 0.0 else 1.0
        micro_f1 = (2 * (micro_recall * micro_precision)) / re_pr

        acc = total_tp / batch_size

        return acc, micro_recall, micro_precision, micro_f1, pred_labels, true_labels, stat_per_label

    def evaluate_results(self, data_loader, pad_id):
        self.log.info("Evaluating test samples...")
        criterion = nn.CrossEntropyLoss(ignore_index=-1)
        total_loss, total_acc, total_f1, total_recall, total_precision = 0.0, 0.0, 0.0, 0.0, 0.0
        all_batch_stats_per_label = []

        self.model.eval()

        for i, data in enumerate(data_loader):
            with torch.no_grad():
                token_ids, e1_e2_start, labels, _, _, _ = data
                attention_mask = (token_ids != pad_id).float()
                token_type_ids = torch.zeros((token_ids.shape[0], token_ids.shape[1])).long()

                labels = labels.to(self.device)

                model_output, pred_classification_logits = self.model(token_ids, token_type_ids=token_type_ids, attention_mask=attention_mask, Q=None,
                            e1_e2_start=e1_e2_start)

                batch_loss = criterion(pred_classification_logits.view(-1, self.model.nclasses).to(self.device), labels.squeeze(1))
                total_loss += batch_loss.item()

                batch_accuracy, batch_recall, batch_precision, batch_f1, pred_labels, true_labels, batch_stats_per_label = \
                    self.evaluate_(pred_classification_logits, labels, ignore_idx=-1)

                all_batch_stats_per_label.append(batch_stats_per_label)

                total_acc += batch_accuracy
                total_recall += batch_recall
                total_precision += batch_precision
                total_f1 += batch_f1

        final_stats_per_label = {}

        for batch_label_stats in all_batch_stats_per_label:
            for label_id, stat_dict in batch_label_stats.items():

                if label_id not in final_stats_per_label.keys():
                    final_stats_per_label[label_id] = stat_dict
                else:
                    for stat, score in stat_dict.items():
                        final_stats_per_label[label_id][stat] += score

        for label_id, stat_dict in final_stats_per_label.items():
            for stat_name, value in stat_dict.items():
                final_stats_per_label[label_id][stat_name] = value / (i + 1)

        total_loss = total_loss / (i + 1)
        total_acc = total_acc / (i + 1)
        total_precision = total_precision / (i + 1)
        total_f1 = total_f1 / (i + 1)
        total_recall = total_recall / (i + 1)

        results = {
            "loss": total_loss,
            "accuracy": total_acc,
            "precision": total_precision,
            "recall": total_recall,
            "f1": total_f1
        }

        print("==================== Evaluation Results ===================")
        print(" no. of batches:", (i + 1))
        for key in sorted(results.keys()):
            print(" %s = %0.3f" %(key, results[key]))
        print("----------------------- class stats -----------------------")
        for label_id, stat_dict in final_stats_per_label.items():
            print("label: %s | f1: %0.3f | prec : %0.3f | acc: %0.3f | recall: %0.3f " % (self.config.general["idx2labels"][label_id],
                stat_dict["f1"],
                stat_dict["prec"],
                stat_dict["acc"],
                stat_dict["recall"]
                ))        
        print("-----------------------------------------------------------")
        print("===========================================================")

        return results

    def pipe(self, stream: Iterable[Doc], *args, **kwargs) -> Iterator[Doc]:

        predict_rel_dataset = RelData(cdb=self.cdb, config=self.config, tokenizer=self.tokenizer)

        self.model = self.model.to(self.device) # type: ignore

        for doc_id, doc in enumerate(stream, 0):
            predict_rel_dataset.dataset, _ = self.create_test_train_datasets(predict_rel_dataset.create_base_relations_from_doc(doc, doc_id), False)

            predict_dataloader = DataLoader(predict_rel_dataset, shuffle=False, batch_size=self.config.train["batch_size"],
                                  num_workers=0, collate_fn=self.padding_seq, pin_memory=self.config.general["pin_memory"])

            total_rel_found = len(predict_rel_dataset.dataset["output_relations"])
            rel_idx = -1

            print("total relations for doc: ", total_rel_found)
            print("processing...")

            pbar = tqdm(total=total_rel_found)

            for i, data in enumerate(predict_dataloader):
                with torch.no_grad():
                    token_ids, e1_e2_start, labels, _, _, _ = data

                    attention_mask = (token_ids != self.pad_id).float()
                    token_type_ids = torch.zeros(token_ids.shape[0], token_ids.shape[1]).long()

                    model_output, pred_classification_logits = self.model(token_ids, token_type_ids=token_type_ids, attention_mask=attention_mask, e1_e2_start=e1_e2_start) # type: ignore

                    for i, pred_rel_logits in enumerate(pred_classification_logits):
                        rel_idx += 1

                        confidence = torch.softmax(pred_rel_logits, dim=0).max(0)
                        predicted_label_id = confidence[1].item()

                        doc._.relations.append({"relation": self.config.general["idx2labels"][str(predicted_label_id)], "label_id": predicted_label_id,
                                                "ent1_text": predict_rel_dataset.dataset["output_relations"][rel_idx][2], 
                                                "ent2_text": predict_rel_dataset.dataset["output_relations"][rel_idx][3],
                                                "confidence": float("{:.3f}".format(confidence[0])),
                                                "start_ent_pos": "",
                                                "end_ent_pos": "",
                                                "start_entity_id": predict_rel_dataset.dataset["output_relations"][rel_idx][8],
                                                "end_entity_id":  predict_rel_dataset.dataset["output_relations"][rel_idx][9]})
                    pbar.update(len(token_ids))
            pbar.close()

            yield doc

    def __call__(self, doc: Doc) -> Doc:
        doc = next(self.pipe(iter([doc])))
        return doc