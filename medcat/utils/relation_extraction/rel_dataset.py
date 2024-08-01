from ast import literal_eval
from typing import Any, Iterable, List, Dict, Tuple, Union
from torch.utils.data import Dataset
from spacy.tokens import Doc
import logging
import pandas
import random
import torch
from medcat.cdb import CDB
from medcat.config_rel_cat import ConfigRelCAT
from medcat.utils.meta_cat.data_utils import Span
from medcat.utils.relation_extraction.tokenizer import TokenizerWrapperBERT


class RelData(Dataset):

    name = "rel_dataset"

    log = logging.getLogger(__name__)

    def __init__(self, tokenizer: TokenizerWrapperBERT, config: ConfigRelCAT, cdb: CDB = CDB()):
        """ Use this class to create a dataset for relation annotations from CSV exports,
            MedCAT exports or Spacy Documents (assuming the documents got generated by MedCAT,
            if they did not then please set the required paramenters manually to match MedCAT output,
            see /medcat/cat.py#_add_nested_ent)

            If you are using this to create relations from CSV it is assumed that your entities/concepts of
            interest are surrounded by the special tokens, see create_base_relations_from_csv doc.

        Args:
            tokenizer (TokenizerWrapperBERT): okenizer used to generate token ids from input text
            config (ConfigRelCAT): same config used in RelCAT
            cdb (CDB): Optional, used to add concept ids and types to detected ents, 
                useful when creating datasets from MedCAT output. Defaults to CDB().
        """

        self.cdb: CDB = cdb
        self.config: ConfigRelCAT = config
        self.tokenizer: TokenizerWrapperBERT = tokenizer
        self.dataset: Dict[Any, Any] = {}

        self.log.setLevel(self.config.general.log_level)

    def generate_base_relations(self, docs: Iterable[Doc]) -> List[Dict]:
        """ Util function, should be used if you want to train from spacy docs

        Args:
            docs (Iterable[Doc]): Generate relations from Spacy CAT docs.

        Returns:
            output_relations: List[Dict] : []      
                "output_relations": relation_instances, <-- see create_base_relations_from_doc/csv
                                                            for data columns
                "nclasses": self.config.model.padding_idx, <-- dummy class
                "labels2idx": {}, 
                "idx2label": {}}
                ]
        """

        output_relations = []
        for doc_id, doc in enumerate(docs):
            output_relations.append(
                self.create_base_relations_from_doc(doc, doc_id=str(doc_id),))

        return output_relations

    def create_base_relations_from_csv(self, csv_path: str, keep_source_text: bool = False):
        """
            Assumes the columns are as follows ["relation_token_span_ids", "ent1_ent2_start", "ent1", "ent2", "label",
            "label_id", "ent1_type", "ent2_type", "ent1_id", "ent2_id", "ent1_cui", "ent2_cui", "doc_id", "sents"],
            last column is the actual source text.

            The entities inside the text MUST be annotated with special tokens i.e:
                ...some text..[s1] first entity [e1].....[s2] second entity [e2]........ 
            You have to store the start position, aka index position of token [e1] and also of token [e2] in
            the (ent1_ent2_start) column.

        Args:
            csv_path (str): path to csv file, must have specific columns, tab separated,
            keep_source_text (bool): if the text clumn should be retained in the 'sents' df column,
                                    used for debugging or creating custom datasets.

        Returns:
            Dict : {  
                "output_relations": relation_instances, <-- see create_base_relations_from_doc/csv
                                                            for data columns
                "nclasses": self.config.model.padding_idx, <-- dummy class
                "labels2idx": {}, 
                "idx2label": {}}
            }
        """

        df = pandas.read_csv(csv_path, index_col=False,
                             encoding='utf-8', sep="\t")

        tmp_col_rel_token_col = df.pop("relation_token_span_ids")

        df.insert(0, "relation_token_span_ids", tmp_col_rel_token_col)

        text_cols = ["sents", "text"]

        df["ent1_ent2_start"] = df["ent1_ent2_start"].apply(
            lambda x: literal_eval(str(x)))

        for col in text_cols:
            if col in df.columns:
                out_rels = []
                for row_idx in range(len(df[col])):
                    _text = df.iloc[row_idx][col]
                    _ent1_ent2_start = df.iloc[row_idx]["ent1_ent2_start"]
                    _rels = self.create_base_relations_from_doc(
                        _text, doc_id=str(row_idx), ent1_ent2_tokens_start_pos=_ent1_ent2_start,)
                    out_rels.append(_rels)

                rows_to_remove = []
                for row_idx in range(len(out_rels)):
                    if len(out_rels[row_idx]["output_relations"]) < 1:
                        rows_to_remove.append(row_idx)

                relation_token_span_ids = []
                out_ent1_ent2_starts = []

                for rel in out_rels:
                    if len(rel["output_relations"]) > 0:
                        relation_token_span_ids.append(
                            rel["output_relations"][0][0])
                        out_ent1_ent2_starts.append(
                            rel["output_relations"][0][1])
                    else:
                        relation_token_span_ids.append([])
                        out_ent1_ent2_starts.append([])

                df["label"] = [i.strip() for i in df["label"]]

                df["relation_token_span_ids"] = relation_token_span_ids
                df["ent1_ent2_start"] = out_ent1_ent2_starts

                df = df.drop(index=rows_to_remove)
                text_col = df.pop(col)
                df = df.assign(col=text_col)
                if keep_source_text:
                    df = df.assign(col=text_col)
                break

        nclasses, labels2idx, idx2label = RelData.get_labels(
            df["label"], self.config)

        output_relations = df.values.tolist()

        self.log.info("CSV dataset | No. of relations detected:" + str(len(output_relations)) +
                      "| from : " + csv_path + " | nclasses: " + str(nclasses) + " | idx2label: " + str(idx2label))

        self.log.info("Samples per class: ")
        for label_num in list(idx2label.keys()):
            sample_count = 0
            for output_relation in output_relations:
                if idx2label[label_num] == output_relation[4]:
                    sample_count += 1
            self.log.info(
                " label: " + idx2label[label_num] + " | samples: " + str(sample_count))

        # replace/update label_id with actual detected label number
        for idx in range(len(output_relations)):
            output_relations[idx][5] = labels2idx[output_relations[idx][4]]

        return {"output_relations": output_relations, "nclasses": nclasses, "labels2idx": labels2idx, "idx2label": idx2label}

    def _create_relation_validation(self, 
                                    text,
                                    doc_id: str,
                                    tokenized_text_data: Dict[str, Any],
                                    ent1_start_char_pos: int,
                                    ent2_start_char_pos: int,
                                    ent1_end_char_pos: int,
                                    ent2_end_char_pos: int,
                                    ent1_token_start_pos: int = -1,
                                    ent2_token_start_pos: int = -1,
                                    ent1_token_end_pos: int = -1,
                                    ent2_token_end_pos: int = -1,
                                    is_spacy_doc: bool = False,
                                    is_mct_export: bool = False,
                                    ) -> Dict:


        text_length = len(text)

        doc_token_length = len(tokenized_text_data["tokens"])

        tmp_doc_text = text

        ent1_token = tmp_doc_text[ent1_start_char_pos: ent1_end_char_pos]
        ent2_token = tmp_doc_text[ent2_start_char_pos: ent2_end_char_pos]

        if abs(ent2_start_char_pos - ent1_start_char_pos) <= self.config.general.window_size:

            ent1_left_ent_context_token_pos_end = ent1_token_start_pos - self.config.general.cntx_left
            left_context_start_char_pos = 0

            if ent1_left_ent_context_token_pos_end < 0:
                ent1_left_ent_context_token_pos_end = 0
            else:
                left_context_start_char_pos = tokenized_text_data["offset_mapping"][ent1_left_ent_context_token_pos_end][0]

            ent2_right_ent_context_token_pos_end = ent2_token_end_pos + self.config.general.cntx_right
            right_context_end_char_pos = text_length - 1

            if ent2_right_ent_context_token_pos_end >= doc_token_length:
                ent2_right_ent_context_token_pos_end = doc_token_length
            else:
                right_context_end_char_pos = tokenized_text_data["offset_mapping"][ent2_right_ent_context_token_pos_end][1]

            if left_context_start_char_pos > right_context_end_char_pos:
                tmp = right_context_end_char_pos
                right_context_end_char_pos = left_context_start_char_pos
                left_context_start_char_pos = tmp

            if is_spacy_doc or is_mct_export:

                tmp_doc_text = text
                _pre_e1 = tmp_doc_text[0: (ent1_start_char_pos)]
                _e1_s2 = tmp_doc_text[(ent1_end_char_pos): (ent2_start_char_pos)]
                _e2_end = tmp_doc_text[(ent2_end_char_pos): text_length]
                ent2_token_end_pos = (ent2_token_end_pos + 2)

                annotation_token_text = self.tokenizer.hf_tokenizers.convert_ids_to_tokens(
                                        self.config.general.annotation_schema_tag_ids)

                tmp_doc_text = _pre_e1 + " " + \
                                                annotation_token_text[0] + " " + \
                                                str(ent1_token) + " " + \
                                                annotation_token_text[1] + " " + _e1_s2 + " " + \
                                                annotation_token_text[2] + " " + str(ent2_token) + " " + \
                                                annotation_token_text[3] + \
                                                " " + _e2_end

                ann_tag_token_len = len(annotation_token_text[0])

                _left_context_start_char_pos = left_context_start_char_pos - ann_tag_token_len - 2 # - 2 spaces
                left_context_start_char_pos = 0 if _left_context_start_char_pos <= 0 \
                    else _left_context_start_char_pos

                _right_context_start_end_pos = right_context_end_char_pos + (ann_tag_token_len * 4) + 8  # 8 for spces
                right_context_end_char_pos = len(tmp_doc_text) if right_context_end_char_pos >= len(tmp_doc_text) or \
                    _right_context_start_end_pos >= len(tmp_doc_text) else _right_context_start_end_pos

                # reassign the new text with added tags
                text_length = len(tmp_doc_text)

            window_tokenizer_data = self.tokenizer(tmp_doc_text[left_context_start_char_pos:right_context_end_char_pos], truncation=True)

            if self.config.general.annotation_schema_tag_ids:
                try:
                    ent1_token_start_pos = \
                        window_tokenizer_data["input_ids"].index(
                            self.config.general.annotation_schema_tag_ids[0])
                    ent2_token_start_pos = \
                        window_tokenizer_data["input_ids"].index(
                            self.config.general.annotation_schema_tag_ids[2])
                    _ent1_token_end_pos = \
                        window_tokenizer_data["input_ids"].index(
                            self.config.general.annotation_schema_tag_ids[1])
                    _ent2_token_end_pos = \
                        window_tokenizer_data["input_ids"].index(
                            self.config.general.annotation_schema_tag_ids[3])
                    assert ent1_token_start_pos
                    assert ent2_token_start_pos
                    assert _ent1_token_end_pos
                    assert _ent2_token_end_pos 
                except Exception:
                    self.log.info("document id: " + str(doc_id) + " failed to process relation")
                    return []

            if not self.config.general.annotation_schema_tag_ids:
                # update token loc to match new selection
                ent2_token_start_pos = ent2_token_start_pos - ent1_token_start_pos
                ent1_token_start_pos = self.config.general.cntx_left if ent1_token_start_pos - self.config.general.cntx_left > 0 else ent1_token_start_pos
                ent2_token_start_pos += ent1_token_start_pos

            ent1_ent2_new_start = (ent1_token_start_pos, ent2_token_start_pos)
            en1_start, en1_end = window_tokenizer_data["offset_mapping"][ent1_token_start_pos]
            en2_start, en2_end = window_tokenizer_data["offset_mapping"][ent2_token_start_pos]

            return [window_tokenizer_data["input_ids"], ent1_ent2_new_start, ent1_token, ent2_token, "UNK", self.config.model.padding_idx,
                                            None, None, None, None, None, None, doc_id, "",
                                            en1_start, en1_end, en2_start, en2_end]
        return []

    def create_base_relations_from_doc(self, doc: Union[Doc, str], doc_id: str, ent1_ent2_tokens_start_pos: Union[List, Tuple] = (-1, -1)) -> Dict:
        """  Creates a list of tuples based on pairs of entities detected (relation, ent1, ent2) for one spacy document or text string.

        Args:
            doc (Union[Doc, str]): SpacyDoc or string of text, each will get handled slightly differently
            doc_id (str): document id
            ent1_ent2_tokens_start_pos (Union[List, Tuple], optional): start of [s1][s2] tokens, if left default
                    we assume we are dealing with a SpacyDoc. Defaults to (-1, -1).

        Returns:
                Dict : {  
                    "output_relations": relation_instances, <-- see create_base_relations_from_doc/csv
                                                                for data columns
                    "nclasses": self.config.model.padding_idx, <-- dummy class
                    "labels2idx": {}, 
                    "idx2label": {}}
                }
        """

        _ent1_start_tkn_id, _ent1_end_tkn_id, _ent2_start_tkn_id, _ent2_end_tkn_id = 0, 0, 0, 0

        chars_to_exclude = ":!@#$%^&*()-+?_=.,;<>/[]{}"

        if self.config.general.annotation_schema_tag_ids:
            # we assume that ent1 start token is pos 0 and ent2 start token is pos 2
            # e.g: [s1], [e1], [s2], [e2]
            _ent1_start_tkn_id = self.config.general.annotation_schema_tag_ids[0]
            _ent1_end_tkn_id = self.config.general.annotation_schema_tag_ids[1]
            _ent2_start_tkn_id = self.config.general.annotation_schema_tag_ids[2]
            _ent2_end_tkn_id = self.config.general.annotation_schema_tag_ids[3]

        relation_instances = []

        tokenized_text_data = None

        if isinstance(doc, str):
            doc_text = doc
        elif isinstance(doc, Doc):
            doc_text = doc.text

        tokenized_text_data = self.tokenizer(doc_text, truncation=False)

        doc_length_tokens = len(tokenized_text_data["tokens"])

        if ent1_ent2_tokens_start_pos != (-1, -1) and isinstance(doc, str):
            ent1_token_start_pos = tokenized_text_data["input_ids"].index(_ent1_start_tkn_id)
            ent2_token_start_pos = tokenized_text_data["input_ids"].index(_ent2_start_tkn_id)
            ent1_token_end_pos = tokenized_text_data["input_ids"].index(_ent1_end_tkn_id)
            ent2_token_end_pos = tokenized_text_data["input_ids"].index(_ent2_end_tkn_id)

            ent1_start_char_pos, ent1_end_char_pos = tokenized_text_data["offset_mapping"][ent1_token_start_pos]
            ent2_start_char_pos, ent2_end_char_pos = tokenized_text_data["offset_mapping"][ent2_token_start_pos]

            relation_instances.append(self._create_relation_validation(text=doc_text,
                                doc_id=doc_id,
                                tokenized_text_data=tokenized_text_data,
                                ent1_start_char_pos=ent1_start_char_pos,
                                ent2_start_char_pos=ent2_start_char_pos,
                                ent1_end_char_pos=ent1_end_char_pos,
                                ent2_end_char_pos=ent2_end_char_pos,
                                ent1_token_start_pos=ent1_token_start_pos,
                                ent2_token_start_pos=ent2_token_start_pos,
                                ent1_token_end_pos=ent1_token_end_pos,
                                ent2_token_end_pos=ent2_token_end_pos
                                ))

        elif isinstance(doc, Doc):
            _ents = doc.ents if len(doc.ents) > 0 else doc._.ents
            for ent1_idx in range(0, len(_ents) - 2):
                ent1_token: Span = _ents[ent1_idx]   # type: ignore

                if str(ent1_token) not in chars_to_exclude and str(ent1_token):
                    ent1_type_id = list(self.cdb.cui2type_ids.get(ent1_token._.cui, ''))
                    ent1_types = [self.cdb.addl_info["type_id2name"].get(tui, '') for tui in ent1_type_id]

                    ent1_start_char_pos = ent1_token.start_char
                    ent1_end_char_pos = ent1_token.end_char

                    ent1_token_start_pos = [i for i in range(0, doc_length_tokens) if ent1_start_char_pos
                                                in range(tokenized_text_data["offset_mapping"][i][0], tokenized_text_data["offset_mapping"][i][1] + 1)][0]
                    ent1_token_end_pos = [i for i in range(0, doc_length_tokens) if ent1_end_char_pos
                                                in range(tokenized_text_data["offset_mapping"][i][0], tokenized_text_data["offset_mapping"][i][1] + 1)][0]

                    for ent2_idx in range((ent1_idx + 1), len(_ents) - 1):
                        ent2_token: Span = _ents[ent2_idx]

                        tmp_ent1 = ent1_token

                        if ent1_token.start_char > ent2_token.start_char:
                            tmp_ent1 = ent1_token
                            ent1_token = ent2_token
                            ent2_token = tmp_ent1

                        if str(ent2_token) not in chars_to_exclude and str(ent1_token) not in self.tokenizer.hf_tokenizers.all_special_tokens and \
                                str(ent2_token) not in self.tokenizer.hf_tokenizers.all_special_tokens and str(ent1_token) != str(ent2_token):

                            ent2_type_id = list(self.cdb.cui2type_ids.get(ent2_token._.cui, ''))
                            ent2_types = [self.cdb.addl_info['type_id2name'].get(tui, '') for tui in ent2_type_id]

                            ent2_start_char_pos = ent2_token.start_char
                            ent2_end_char_pos = ent2_token.end_char

                            ent2_token_start_pos = [i for i in range(0, doc_length_tokens) if ent2_start_char_pos
                                                        in range(tokenized_text_data["offset_mapping"][i][0], tokenized_text_data["offset_mapping"][i][1] + 1)][0]

                            ent2_token_end_pos = [i for i in range(0, doc_length_tokens) if ent2_end_char_pos
                                                in range(tokenized_text_data["offset_mapping"][i][0], tokenized_text_data["offset_mapping"][i][1] + 1)][0]

                            if self.config.general.relation_type_filter_pairs:
                                for rel_pair in self.config.general.relation_type_filter_pairs:
                                    if rel_pair[0] in ent1_types and rel_pair[1] in ent2_types:
                                        relation_instances.append(self._create_relation_validation(text=doc_text,
                                                doc_id=doc_id,
                                                tokenized_text_data=tokenized_text_data,
                                                ent1_start_char_pos=ent1_start_char_pos,
                                                ent2_start_char_pos=ent2_start_char_pos,
                                                ent1_end_char_pos=ent1_end_char_pos,
                                                ent2_end_char_pos=ent2_end_char_pos,
                                                ent1_token_start_pos=ent1_token_start_pos,
                                                ent2_token_start_pos=ent2_token_start_pos,
                                                ent1_token_end_pos=ent1_token_end_pos,
                                                ent2_token_end_pos=ent2_token_end_pos,
                                                is_spacy_doc=True
                                        ))
                            else:
                                relation_instances.append(self._create_relation_validation(text=doc_text,
                                                    doc_id=doc_id,
                                                    tokenized_text_data=tokenized_text_data,
                                                    ent1_start_char_pos=ent1_start_char_pos,
                                                    ent2_start_char_pos=ent2_start_char_pos,
                                                    ent1_end_char_pos=ent1_end_char_pos,
                                                    ent2_end_char_pos=ent2_end_char_pos,
                                                    ent1_token_start_pos=ent1_token_start_pos,
                                                    ent2_token_start_pos=ent2_token_start_pos,
                                                    ent1_token_end_pos=ent1_token_end_pos,
                                                    ent2_token_end_pos=ent2_token_end_pos,
                                                    is_spacy_doc=True
                                    ))

                        # restore ent1
                        ent1_token = tmp_ent1

        # cleanup
        relation_instances = [rel for rel in relation_instances if rel != []]

        return {"output_relations": relation_instances, "nclasses": self.config.model.padding_idx, "labels2idx": {}, "idx2label": {}}

    def create_relations_from_export(self, data: Dict):
        """  
            Args:
                data (Dict):
                    MedCAT Export data.

            Returns:
                Dict : {  
                    "output_relations": relation_instances, <-- see create_base_relations_from_doc/csv
                                                                for data columns
                    "nclasses": self.config.model.padding_idx, <-- dummy class
                    "labels2idx": {}, 
                    "idx2label": {}}
                }
        """

        output_relations = []

        for project in data["projects"]:
            for doc_id, document in enumerate(project["documents"]):
                doc_text = str(document["text"])

                if len(doc_text) > 0:
                    annotations = document["annotations"]
                    relations = document["relations"]

                    if self.config.general.lowercase:
                        doc_text = doc_text.lower()

                    tokenizer_text_data = self.tokenizer(doc_text, truncation=False)

                    doc_token_length = len(tokenizer_text_data["tokens"])

                    relation_instances = []
                    ann_ids_from_relations = []

                    ann_ids_ents: Dict[Any, Any] = {}

                    _other_relations_subset = []

                    # this section creates 'Other' class relations based on validated annotations
                    for ent1_idx, ent1_ann in enumerate(annotations):
                        ann_id = ent1_ann["id"]
                        ann_ids_ents[ann_id] = {}
                        ann_ids_ents[ann_id]["cui"] = ent1_ann["cui"]
                        ann_ids_ents[ann_id]["type_ids"] = list(self.cdb.cui2type_ids.get(ent1_ann["cui"], ""))
                        ann_ids_ents[ann_id]["types"] = [self.cdb.addl_info['type_id2name'].get(tui, '') for tui in ann_ids_ents[ann_id]['type_ids']]

                        ent1_types = ann_ids_ents[ann_id]["types"]

                        if self.config.general.create_addl_rels:
                            for _, ent2_ann in enumerate(annotations[ent1_idx + 1:]):
                                ent2_types = list(self.cdb.cui2type_ids.get(ent2_ann["cui"], ""))

                                if ent1_ann["validated"] and ent2_ann["validated"]:
                                    _relation_type = "Other"

                                    # create new Other subclass class if enabled
                                    if self.config.general.create_addl_rels_by_type:
                                        _relation_type = "Other" + ent1_types[0] + "-" + ent2_types[0]

                                    _other_relations_subset.append({
                                        "start_entity": ent1_ann["id"],
                                        "start_entity_cui": ent1_ann["cui"],
                                        "start_entity_value": ent1_ann["value"],
                                        "start_entity_start_idx": ent1_ann["start"],
                                        "start_entity_end_idx": ent1_ann["end"],
                                        "end_entity": ent2_ann["id"],
                                        "end_entity_cui": ent2_ann["cui"],
                                        "end_entity_value": ent2_ann["value"],
                                        "end_entity_start_idx": ent2_ann["start"],
                                        "end_entity_end_idx": ent2_ann["end"],
                                        "relation": _relation_type,
                                        "validated": True
                                    })

                    non_rel_sample_size_limit = int(int(self.config.general.addl_rels_max_sample_size) / len(data['projects']))

                    if non_rel_sample_size_limit > 0 and len(_other_relations_subset) > 0:
                        random.shuffle(_other_relations_subset)
                        _other_relations_subset = _other_relations_subset[0:non_rel_sample_size_limit]

                    relations.extend(_other_relations_subset)

                    for relation in relations:
                        ann_start_start_pos = relation['start_entity_start_idx']
                        ann_start_end_pos = relation["start_entity_end_idx"]

                        ann_end_start_pos = relation['end_entity_start_idx']
                        ann_end_end_pos = relation["end_entity_end_idx"]

                        start_entity_value = relation['start_entity_value']
                        end_entity_value = relation['end_entity_value']

                        start_entity_id = relation['start_entity']
                        end_entity_id = relation['end_entity']

                        start_entity_types = ann_ids_ents[start_entity_id]['types']
                        end_entity_types = ann_ids_ents[end_entity_id]['types']
                        start_entity_cui = ann_ids_ents[start_entity_id]['cui']
                        end_entity_cui = ann_ids_ents[end_entity_id]['cui']

                        # if somehow the annotations belong to the same relation but make sense in reverse
                        if ann_start_start_pos > ann_end_start_pos:
                            ann_end_start_pos = relation['start_entity_start_idx']
                            ann_end_end_pos = relation['start_entity_end_idx']

                            ann_start_start_pos = relation['end_entity_start_idx']
                            ann_start_end_pos = relation['end_entity_end_idx']

                            end_entity_value = relation['start_entity_value']
                            start_entity_value = relation['end_entity_value']

                            end_entity_cui = ann_ids_ents[start_entity_id]['cui']
                            start_entity_cui = ann_ids_ents[end_entity_id]['cui']

                            end_entity_types = ann_ids_ents[start_entity_id]['types']
                            start_entity_types = ann_ids_ents[end_entity_id]['types']

                            # switch ids last
                            start_entity_id = relation['end_entity']
                            end_entity_id = relation['start_entity']

                        for ent1type, ent2type in enumerate(self.config.general.relation_type_filter_pairs):
                            if ent1type not in start_entity_types and ent2type not in end_entity_types:
                                continue

                        ann_ids_from_relations.extend([start_entity_id, end_entity_id])
                        relation_label = relation['relation'].strip()

                        ent1_token_start_pos = [i for i in range(0, doc_token_length) if ann_start_start_pos
                                                        in range(tokenizer_text_data["offset_mapping"][i][0], tokenizer_text_data["offset_mapping"][i][1] + 1)][0]

                        ent2_token_start_pos = [i for i in range(0, doc_token_length) if ann_end_start_pos
                                                        in range(tokenizer_text_data["offset_mapping"][i][0], tokenizer_text_data["offset_mapping"][i][1] + 1)][0]

                        ent1_token_end_pos = [i for i in range(0, doc_token_length) if ann_start_end_pos
                                                        in range(tokenizer_text_data["offset_mapping"][i][0], tokenizer_text_data["offset_mapping"][i][1] + 1)][0]

                        ent2_token_end_pos = [i for i in range(0, doc_token_length) if ann_end_end_pos
                                                        in range(tokenizer_text_data["offset_mapping"][i][0], tokenizer_text_data["offset_mapping"][i][1] + 1)][0]

                        if start_entity_id != end_entity_id and relation.get('validated', True) and start_entity_value not in self.tokenizer.all_special_tokens and end_entity_value not in self.tokenizer.all_special_tokens :
                            final_relation = self._create_relation_validation(text=doc_text,
                                    doc_id=doc_id,
                                    tokenized_text_data=tokenizer_text_data,
                                    ent1_start_char_pos=ann_start_start_pos,
                                    ent2_start_char_pos=ann_end_start_pos,
                                    ent1_end_char_pos=ann_start_end_pos,
                                    ent2_end_char_pos=ann_end_end_pos,
                                    ent1_token_start_pos=ent1_token_start_pos,
                                    ent2_token_start_pos=ent2_token_start_pos,
                                    ent1_token_end_pos=ent1_token_end_pos,
                                    ent2_token_end_pos=ent2_token_end_pos,
                                    is_mct_export=True
                            )

                            if len(final_relation) > 0:
                                final_relation[4] = relation_label
                                final_relation[6] = start_entity_types
                                final_relation[7] = end_entity_types
                                final_relation[8] = start_entity_id
                                final_relation[9] = end_entity_id
                                final_relation[10] = start_entity_cui
                                final_relation[11] = end_entity_cui

                                relation_instances.append(final_relation)

                    output_relations.extend(relation_instances)

        all_relation_labels = [relation[4] for relation in output_relations]

        nclasses, labels2idx, idx2label = self.get_labels(
            all_relation_labels, self.config)

        # replace label_id with actual detected label number
        for idx in range(len(output_relations)):
            output_relations[idx][5] = labels2idx[output_relations[idx][4]]

        self.log.info("MCT export dataset | nclasses: " +
                      str(nclasses) + " | idx2label: " + str(idx2label))
        self.log.info("Samples per class: ")
        for label_num in list(idx2label.keys()):
            sample_count = 0
            for output_relation in output_relations:
                if idx2label[label_num] == output_relation[4]:
                    sample_count += 1
            self.log.info(
                " label: " + idx2label[label_num] + " | samples: " + str(sample_count))

        return {"output_relations": output_relations, "nclasses": nclasses, "labels2idx": labels2idx, "idx2label": idx2label}

    @classmethod
    def get_labels(cls, relation_labels: List[str], config: ConfigRelCAT) -> Tuple[int, Dict[str, Any], Dict[int, Any]]:
        """ This is used to update labels in config with unencountered classes/labels ( if any are encountered during training).

        Args:
            relation_labels (List[str]): new labels to add
            config (ConfigRelCAT): config

        Returns:
            Any: _description_
        """
        curr_class_id = 0

        config_labels2idx: Dict = config.general.labels2idx
        config_idx2labels: Dict = config.general.idx2labels

        relation_labels = [relation_label.strip()
                           for relation_label in relation_labels]

        for relation_label in set(relation_labels):
            if relation_label not in config_labels2idx.keys():
                while curr_class_id in [int(label_idx) for label_idx in config_idx2labels.keys()]:
                    curr_class_id += 1
                config_labels2idx[relation_label] = curr_class_id
                config_idx2labels[curr_class_id] = relation_label

        return len(config_labels2idx.keys()), config_labels2idx, config_idx2labels,

    def __len__(self) -> int:
        """
        Returns:
            int: num of rels records
        """
        return len(self.dataset['output_relations'])

    def __getitem__(self, idx: int) -> Tuple[torch.LongTensor, torch.LongTensor, torch.LongTensor]:
        """

        Args:
            idx (int): index of item in the dataset dict

        Returns:
            Tuple[torch.LongTensor, torch.LongTensor, torch.LongTensor]: long tensors of the following the columns : input_ids, ent1&ent2 token start pos idx, label_ids
        """

        return torch.LongTensor(self.dataset['output_relations'][idx][0]),\
            torch.LongTensor(self.dataset['output_relations'][idx][1]),\
            torch.LongTensor([self.dataset['output_relations'][idx][5]])
