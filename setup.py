from setuptools import setup, find_packages

setup(
    name="qmt-rpyc-client",
    version="0.2.1",
    description="RPyC client for xtquant (QMT/MiniQMT) - cross-platform access",
    packages=find_packages(include=["client*", "common*"]),
    install_requires=["rpyc>=6.0.0"],
    python_requires=">=3.8",
)
