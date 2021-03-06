from __future__ import unicode_literals

from datetime import datetime

import pytest
from botocore.exceptions import ClientError

from .conftest import ordered
from .. import ebs


def test_get_account_id(sts_stub):
    sts_stub.add_response(
        'get_caller_identity',
        {
            'UserId': 'AKIAI44QH8DHBEXAMPLE',
            'Account': '23456789012',
            'Arn': 'arn:aws:iam::123456789012:user/Alice'
        })

    assert ebs.get_account_id() == '23456789012'
    # Call twice to check if the result is cached
    assert ebs.get_account_id() == '23456789012'

    sts_stub.assert_no_pending_responses()


def test_get_instance_info(ec2_stub, mocker):
    instance_id = 'i-12345678'
    instance_info = {'InstanceId': instance_id}

    ec2_stub.add_response(
        'describe_instances',
        {
            'Reservations': [{
                'Instances': [instance_info]

            }]
        },
        {'InstanceIds': [instance_id], 'DryRun': False})

    assert ebs.get_instance_info(instance_id) == instance_info
    ec2_stub.assert_no_pending_responses()


def test_get_instance_info_not_found(ec2_stub, mocker):
    instance_id = 'i-12345678'

    ec2_stub.add_client_error(
        'describe_instances',
        service_error_code='InvalidInstanceID.NotFound',
        expected_params={'InstanceIds': [instance_id], 'DryRun': False})

    assert ebs.get_instance_info(instance_id) is None
    ec2_stub.assert_no_pending_responses()


def test_get_instance_info_error(ec2_stub, mocker):
    instance_id = 'i-12345678'

    ec2_stub.add_client_error(
        'describe_instances',
        service_error_code='InvalidInstanceID.Malformed',
        expected_params={'InstanceIds': [instance_id], 'DryRun': False})

    with pytest.raises(ClientError):
        ebs.get_instance_info(instance_id)


@pytest.mark.parametrize('filters,tags,result', [
    (
        [],
        [],
        []
    ),
    (
        [],
        [('a', 'b'), ('c', 'd')],
        [
            {'Name': 'tag:a', 'Values': ['b']},
            {'Name': 'tag:c', 'Values': ['d']}
        ]
    ),
    (
        [{'Name': 'test', 'Values': ['test']}],
        [],
        [{'Name': 'test', 'Values': ['test']}]
    )
])
def test_filters_with_tags(filters, tags, result):
    assert ebs._filters_with_tags(filters, tags) == result


def test_find_attached_volumes(ec2_stub, mocker):
    instance_id = 'i-12345678'
    tags = [('a', 'b')]
    base_filters = [
        {'Name': 'test', 'Values': ['test']}
    ]
    filters = base_filters + [
        {'Name': 'tag:a', 'Values': ['b']},
        {'Name': 'attachment.instance-id', 'Values': [instance_id]},
        {'Name': 'attachment.status', 'Values': ['attached', 'attaching']}
    ]
    volume_1 = {'VolumeId': 'vol-11111111'}
    volume_2 = {'VolumeId': 'vol-22222222'}
    next_token = 'whatever'

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [volume_1],
            'NextToken': next_token
        },
        {'Filters': filters, 'DryRun': False})

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [volume_2]
        },
        {'Filters': filters, 'DryRun': False, 'NextToken': next_token})

    assert (ebs.find_attached_volumes(tags, {'InstanceId': instance_id},
                                      base_filters) ==
            [volume_1, volume_2])
    ec2_stub.assert_no_pending_responses()


def test_find_available_volumes(ec2_stub, mocker):
    instance_id = 'i-12345678'
    az = 'us-east-1'
    instance_info = {
        'InstanceId': instance_id,
        'Placement': {'AvailabilityZone': az}
    }

    tags = [('a', 'b')]
    base_filters = [
        {'Name': 'test', 'Values': ['test']}
    ]
    filters = base_filters + [
        {'Name': 'tag:a', 'Values': ['b']},
        {'Name': 'status', 'Values': ['creating', 'available']},
        {'Name': 'availability-zone', 'Values': [az]}
    ]

    volume_1 = {'VolumeId': 'vol-11111111'}
    volume_2 = {'VolumeId': 'vol-22222222'}
    next_token = 'whatever'

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [volume_1],
            'NextToken': next_token
        },
        {'Filters': filters, 'DryRun': False})

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [volume_2]
        },
        {'Filters': filters, 'DryRun': False, 'NextToken': next_token})

    actual_volumes = ebs.find_available_volumes(tags, instance_info,
                                                base_filters)
    assert ordered(actual_volumes) == ordered([volume_1, volume_2])

    ec2_stub.assert_no_pending_responses()


def test_find_existing_snapshots(ec2_stub, mocker):
    account_id = '23456789012'
    mocker.patch('ebs_snatcher.ebs.get_account_id', return_value=account_id)

    tags = [('a', 'b')]
    base_filters = [
        {'Name': 'test', 'Values': ['test']}
    ]
    filters = base_filters + [
        {'Name': 'tag:a', 'Values': ['b']},
        {'Name': 'status', 'Values': ['completed']}
    ]

    snap_old = {
        'SnapshotId': 'snap-11111111',
        'StartTime': datetime(2017, 1, 1, 0, 0, 0)
    }
    snap_new = {
        'SnapshotId': 'snap-22222222',
        'StartTime': datetime(2017, 2, 1, 0, 0, 0)
    }
    next_token = 'whatever'

    params = {
        'Filters': filters,
        'DryRun': False,
        'RestorableByUserIds': [account_id]
    }

    ec2_stub.add_response(
        'describe_snapshots',
        {
            'Snapshots': [snap_old],
            'NextToken': next_token
        },
        params.copy())

    params['NextToken'] = next_token
    ec2_stub.add_response(
        'describe_snapshots',
        {
            'Snapshots': [snap_new]
        },
        params)

    assert \
        ebs.find_existing_snapshot(tags, base_filters) == snap_new
    ec2_stub.assert_no_pending_responses()


def test_create_volume(ec2_stub):
    az = 'us-east-1'
    volume_type = 'gp2'

    id_tags = [('a', 'b')]
    extra_tags = [('c', 'd')]

    iops = 1000
    kms_key_id = 'key-111111111'
    snapshot_id = 'snap-11111111'
    volume_id = 'vol-11111111'

    params = {
        'AvailabilityZone': az,
        'VolumeType': volume_type,
        'Iops': 1000,
        'Encrypted': True,
        'KmsKeyId': kms_key_id,
        'SnapshotId': snapshot_id,
        'DryRun': False,
        'TagSpecifications': [{
           'ResourceType': 'volume',
           'Tags': [
               {'Key': 'a', 'Value': 'b'},
               {'Key': 'c', 'Value': 'd'}
           ]
        }]
    }

    ec2_stub.add_response(
        'create_volume',
        {'VolumeId': volume_id},
        params)

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'available'
            }]
        },
        {'VolumeIds': [volume_id], 'DryRun': False}
    )

    volume = ebs.create_volume(
        id_tags=id_tags,
        extra_tags=extra_tags,
        availability_zone=az,
        volume_type=volume_type,
        size=None,
        iops=iops,
        kms_key_id=kms_key_id,
        src_snapshot_id=snapshot_id)

    assert volume['VolumeId'] == volume_id
    ec2_stub.assert_no_pending_responses()


@pytest.mark.parametrize('prefix', ['sd', 'xvd', '/dev/sd', '/dev/xvd'])
@pytest.mark.parametrize('dev,next_dev', [
    ('a', 'b'),
    ('b', 'c'),
    ('y', 'z'),
    ('z', 'aa'),
    ('aa', 'ab'),
    ('az', 'ba'),
    ('bz', 'ca'),
    ('zz', 'aaa')
])
def test_next_device_name(prefix, dev, next_dev):
    assert ebs.next_device_name(prefix + dev) == prefix + next_dev


def test_attach_volume_default_device(ec2_stub):
    volume_id = 'vol-11111111'
    instance_id = 'i-11111111'
    device_name = '/dev/sdf'

    # Add responses for the available waiter
    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'creating'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'available'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    # Successfull attachment
    ec2_stub.add_response(
        'attach_volume',
        {
            'VolumeId': volume_id,
            'InstanceId': instance_id,
            'State': 'attaching',
            'Device': device_name,
            'AttachTime': datetime(2017, 1, 1, 0, 0, 0)
        },
        {
            'Device': device_name,
            'InstanceId': instance_id,
            'VolumeId': volume_id,
            'DryRun': False
        })

    # Wait until attachment finishes
    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'in-use'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'Filters': [{'Name': 'attachment.status', 'Values': ['attached']}],
            'DryRun': False
        })

    # Don't pass device name, so the default is used.
    assert (ebs.attach_volume(volume_id, {'InstanceId': instance_id}) ==
            device_name)
    ec2_stub.assert_no_pending_responses()


def test_attach_volume_failure(ec2_stub):
    volume_id = 'vol-11111111'
    instance_id = 'i-11111111'
    device_name = '/dev/sdf'

    # Add responses for the available waiter
    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'creating'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'available'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    # Fail the attachment. Any error should work as long as it is not a
    # 'device already in use' error.
    ec2_stub.add_client_error(
        'attach_volume',
        expected_params={
            'Device': device_name,
            'InstanceId': instance_id,
            'VolumeId': volume_id,
            'DryRun': False
        })

    with pytest.raises(ClientError):
        ebs.attach_volume(volume_id, {'InstanceId': instance_id}, device_name)


def test_attach_volume_device_in_use_retry(ec2_stub):
    volume_id = 'vol-11111111'
    instance_id = 'i-11111111'
    device_in_use = '/dev/sdf'
    device_ok = '/dev/sdg'

    # Add responses for the available waiter
    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'creating'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'available'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'DryRun': False
        })

    # Fail the first attachment. Any error should work as long as it is not a
    # 'device already in use' error.
    error_msg = 'Attachment point {} is already in use'.format(device_in_use)
    ec2_stub.add_client_error(
        'attach_volume',
        service_error_code='InvalidParameterValue',
        service_message=error_msg,
        expected_params={
            'Device': device_in_use,
            'InstanceId': instance_id,
            'VolumeId': volume_id,
            'DryRun': False
        })

    # Expected a retry with the next device name
    ec2_stub.add_response(
        'attach_volume',
        {
            'VolumeId': volume_id,
            'InstanceId': instance_id,
            'State': 'attaching',
            'Device': device_ok,
            'AttachTime': datetime(2017, 1, 1, 0, 0, 0)
        },
        {
            'Device': device_ok,
            'InstanceId': instance_id,
            'VolumeId': volume_id,
            'DryRun': False
        })

    # Wait until attachment finishes
    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'in-use'
            }]
        },
        {
            'VolumeIds': [volume_id],
            'Filters': [{'Name': 'attachment.status', 'Values': ['attached']}],
            'DryRun': False
        })

    assert (ebs.attach_volume(volume_id, {'InstanceId': instance_id}) ==
            device_ok)


def test_delete_volume(ec2_stub, volume_id):
    params = {
        'VolumeId': volume_id,
        'DryRun': False
    }

    ec2_stub.add_response(
        'delete_volume',
        {},
        params)

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'deleting'
            }]
        },
        {'VolumeIds': [volume_id], 'DryRun': False}
    )

    ec2_stub.add_response(
        'describe_volumes',
        {
            'Volumes': [{
                'VolumeId': volume_id,
                'State': 'deleted'
            }]
        },
        {'VolumeIds': [volume_id], 'DryRun': False}
    )

    assert ebs.delete_volume(volume_id=volume_id) is None
    ec2_stub.assert_no_pending_responses()


DEV_TEST_VOLUME_ID = 'vol-12345678'
DEV_TEST_NVME_PATH = \
    '/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol12345678'
DEV_TEST_XEN_PATH = '/dev/xvda'
DEV_TEST_EBS_PATH = '/dev/sda'
DEV_TEST_SCSI_PATH = DEV_TEST_EBS_PATH


@pytest.mark.parametrize('nvme_exists,xen_exists,scsi_exists,expected_path', [
    (False, False, False, DEV_TEST_SCSI_PATH),
    (False, False, True, DEV_TEST_SCSI_PATH),
    (False, True, False, DEV_TEST_XEN_PATH),
    (False, True, True, DEV_TEST_XEN_PATH),
    (True, False, False, DEV_TEST_NVME_PATH),
    (True, False, True, DEV_TEST_NVME_PATH),
    (True, True, False, DEV_TEST_NVME_PATH),
    (True, True, True, DEV_TEST_NVME_PATH),
])
def test_find_system_block_device(mocker, nvme_exists, xen_exists, scsi_exists,
                                  expected_path):
    def path_exists(path):
        if path == DEV_TEST_NVME_PATH:
            return nvme_exists
        elif path == DEV_TEST_XEN_PATH:
            return xen_exists
        elif path == DEV_TEST_SCSI_PATH:
            return scsi_exists

        assert False

    mocker.patch('os.path.exists', side_effect=path_exists)
    sleep = mocker.Mock()

    actual_path = ebs.find_system_block_device(
        DEV_TEST_VOLUME_ID, DEV_TEST_EBS_PATH, retries=1, sleep=sleep)

    assert actual_path == expected_path


def test_find_system_block_device_retry(mocker):
    mocker.patch('os.path.exists', return_value=False)
    sleep = mocker.Mock()

    retries = 2
    ebs.find_system_block_device(
        DEV_TEST_VOLUME_ID, DEV_TEST_EBS_PATH, retries=2, sleep=sleep)

    assert sleep.call_count == retries
    sleep.assert_called_with(10.0)
