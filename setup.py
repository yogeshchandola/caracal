#!/usr/bin/env python

import os

try:
  from setuptools import setup
except ImportError as e:
  from distutils.core import setup

requirements = [
'ruamel.yaml==0.15.22',
'stimela>=0.3.2',
'numpy>=1.13.1',
'stimela>=0.3.2',
'scipy>=0.19.1',
'pysolr>=3.4.0',
'progressbar2>=3.11.0',
'nbconvert>=5.3.1',
'aplpy>=1.1.1',
'matplotlib>=2.1.0',
'tornado>=4.0.0,<5.0.0',
'jupyter>=1.0.0',
'pykwalify>=1.6.0',
'yamlordereddictloader',
'astropy<=3.0.4',
'astroquery>=0.3.8',
]

PACKAGE_NAME = 'meerkathi'
__version__ = '0.1.0'

setup(name = PACKAGE_NAME,
    version = __version__,
    description = "MeerKAT end-to-end data reduction pipeline",
    author = "MeerKATHI peeps",
    author_email = "sphemakh@gmail.com",
    url = "https://github.com/ska-sa/meerkathi",
    packages=[PACKAGE_NAME], 
    install_requires = requirements,
    include_package_data = True,
    ##package_data - any binary or meta data files should go into MANIFEST.in
    scripts = ["bin/" + j for j in os.listdir("bin")],
    license=["GNU GPL v2"],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python",
        "Topic :: Scientific/Engineering :: Astronomy"
    ]
     )
