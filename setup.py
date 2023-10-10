import setuptools

with open("./README.md", "r") as fh:
    long_description = fh.read()


setuptools.setup(
    name="medcat",
    setup_requires=["setuptools_scm"],
    use_scm_version={"local_scheme": "no-local-version", "fallback_version": "unknown"},
    author="w-is-h",
    author_email="w.kraljevic@gmail.com",
    description="Concept annotation tool for Electronic Health Records",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/CogStack/MedCAT",
<<<<<<< HEAD
    packages=['medcat', 'medcat.utils', 'medcat.preprocessing', 'medcat.cogstack', 'medcat.ner', 'medcat.linking', 'medcat.datasets',
              'medcat.tokenizers', 'medcat.utils.meta_cat', 'medcat.pipeline', 'medcat.neo', 'medcat.utils.ner'],
=======
    packages=['medcat', 'medcat.utils', 'medcat.preprocessing', 'medcat.ner', 'medcat.linking', 'medcat.datasets',
              'medcat.tokenizers', 'medcat.utils.meta_cat', 'medcat.pipeline', 'medcat.utils.ner',
              'medcat.utils.saving', 'medcat.utils.regression'],
>>>>>>> ab07daa (CU-8692wgmkm: Remove py2neo dependency and the code that used it (#356))
    install_requires=[
        'numpy>=1.21.4',
        'pandas<=1.4.2,>=1.1.5',
        'gensim~=4.1.2',
        'spacy==3.7.0', # seems to be needed to work in 2023
        'scipy<=1.8.1,>=1.5.4',
        'transformers~=4.19.2',
        'torch>=1.0',
        'tqdm>=4.27',
        'scikit-learn<1.2.0',
        'elasticsearch>=8.3,<9',  # Check if this is compatible with opensearch otherwise: 'elasticsearch>=7.10,<8.0.0',
        'eland>=8.3.0,<9',
        'dill~=0.3.4,<0.3.5', # less than 0.3.5 due to datasets requirement
        'datasets~=2.2.2',
        'jsonpickle~=2.0.0',
        'psutil<6.0.0,>=5.8.0',
        # 0.70.12 uses older version of dill (i.e less than 0.3.5) which is required for datasets
        'multiprocess==0.70.12',  # seems to work better than standard mp
        'aiofiles~=0.8.0',
        'ipywidgets~=7.6.5',
        'xxhash==3.0.0',
        'blis<=0.7.5',
        'click<=8.0.4',  # Spacy breaks without this
        'pydantic==1.10.13', # seems to be needed to work in 2023
        # the following are not direct dependencies of MedCAT but needed for docs/building
        'aiohttp==3.8.3', # 3.8.3 is needed for compatibility with fsspec
        'smart-open==5.2.1', # 5.2.1 is needed for compatibility with pathy
        'joblib~=1.2', 
        ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
