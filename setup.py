from setuptools import setup, find_packages
from pathlib import Path

reqs = [
    r.strip() for r in
    Path(__file__).parent.joinpath("requirements.txt").read_text().splitlines()
    if r.strip() and not r.startswith("#")
]

setup(
    name="mlb_core",
    version="0.1.0",
    packages=find_packages(),
    install_requires=reqs,
)
