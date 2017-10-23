from setuptools import setup

VERSION = '0.1.1'

setup(
    name='ebs-snatcher',
    packages=['ebs_snatcher'],
    version=VERSION,
    description='Automatically provision AWS EBS volumes from snapshots',
    long_description=open('README.rst').read(),
    url='https://github.com/Cobliteam/ebs-snatcher',
    download_url='https://github.com/Cobliteam/ebs-snatcher/archive/{}.tar.gz'.format(VERSION),
    author='Daniel Miranda',
    author_email='daniel@cobli.co',
    license='MIT',
    install_requires=[
        'boto3',
        'future'
    ],
    entry_points={
        'console_scripts': ['ebs-snatcher=ebs_snatcher.main:main']
    },
    keywords='aws ebs')
