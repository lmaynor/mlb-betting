
from setuptools import setup, find_packages

setup(

    name="mlb_core",

    version="0.1.0",

    packages=find_packages(),

    install_requires=[

        "pandas",

        "numpy",

        "xgboost",

        "scikit-learn",

        "selenium",

        "requests",

        "pybaseball",

    ],

)

