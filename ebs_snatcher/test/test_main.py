from unittest import mock

import pytest
import boto3
from botocore.stub import Stubber

from ebs_snatcher import main


def boto3_stub(mocker, svc):
    client = boto3.client(svc)
    stub = Stubber(client)
    mocker.patch('ebs_snatcher.main.' + svc, return_value=client)
    stub.activate()
    yield stub
    stub.deactivate()


@pytest.fixture
def ec2_stub(mocker):
    yield from boto3_stub(mocker, 'ec2')


@pytest.fixture
def sts_stub(mocker):
    yield from boto3_stub(mocker, 'sts')


def test_get_account_id(sts_stub):
    sts_stub.add_response(
        'get_caller_identity',
        {
            'UserId': 'AKIAI44QH8DHBEXAMPLE',
            'Account': '23456789012',
            'Arn': 'arn:aws:iam::123456789012:user/Alice'
        })

    assert main.get_account_id() == '23456789012'
    # Call twice to check if the result is cached
    assert main.get_account_id() == '23456789012'

    sts_stub.assert_no_pending_responses()
