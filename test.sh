#!/bin/sh

set -e

flake8 ebs_snatcher
pytest --cov=ebs_snatcher ebs_snatcher/test
