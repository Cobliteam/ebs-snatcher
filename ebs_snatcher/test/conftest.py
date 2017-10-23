import pytest
import boto3
from botocore import UNSIGNED
from botocore.client import Config
from botocore.stub import Stubber


def boto3_stub(mocker, svc):
    client = boto3.client(svc, config=Config(signature_version=UNSIGNED),
                          region_name='us-east-1')
    mocker.patch('ebs_snatcher.ebs.' + svc, return_value=client)

    orig_get_waiter = type(client).get_waiter

    def get_waiter_no_delay(*args, **kwargs):
        waiter = orig_get_waiter(client, *args, **kwargs)
        waiter.config.delay = 0
        return waiter

    mocker.patch.object(client, 'get_waiter', wraps=get_waiter_no_delay)

    stub = Stubber(client)
    stub.activate()

    yield stub

    stub.deactivate()


@pytest.fixture
def ec2_stub(mocker):
    yield from boto3_stub(mocker, 'ec2')


@pytest.fixture
def sts_stub(mocker):
    yield from boto3_stub(mocker, 'sts')
