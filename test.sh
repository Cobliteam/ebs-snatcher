#!/bin/sh

set -e

flake8 ebs_snatcher
coverage erase
coverage run --source ebs_snatcher -m py.test
coverage report --include='ebs_snatcher/**' --omit='ebs_snatcher/test/**'
