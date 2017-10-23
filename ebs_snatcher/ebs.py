import re
import logging
from itertools import chain

import boto3
from botocore.exceptions import ClientError

from .util import memoize


VOLUME_TYPES = set(['standard', 'gp2', 'io1', 'sc1', 'st1'])

logger = logging.getLogger('ebs-snatcher.ebs')
ec2 = memoize(lambda: boto3.client('ec2'))
sts = memoize(lambda: boto3.client('sts'))


@memoize
def get_account_id():
    return sts().get_caller_identity()['Account']


def get_instance_info(instance_id):
    logger.debug('Retrieving instance info for ID %s', instance_id)

    try:
        response = ec2().describe_instances(InstanceIds=[instance_id],
                                            DryRun=False)
        return response['Reservations'][0]['Instances'][0]
    except ClientError as e:
        if e.response['Error']['Code'] != 'InvalidInstanceID.NotFound':
            raise

        return None


def _filters_with_tags(filters, tag_pairs):
    filters = list(filters)
    for k, v in tag_pairs:
        filters.append({'Name': 'tag:{}'.format(k), 'Values': [v]})

    return filters


def find_attached_volumes(id_tags, instance_info, filters=()):
    instance_id = instance_info['InstanceId']

    filters = _filters_with_tags(filters, id_tags)
    filters.extend([
        {'Name': 'attachment.instance-id', 'Values': [instance_id]},
        {'Name': 'attachment.status', 'Values': ['attached', 'attaching']}
    ])

    paginator = ec2().get_paginator('describe_volumes')
    volumes = []
    for response in paginator.paginate(Filters=filters, DryRun=False):
        volumes.extend(response['Volumes'])

    return volumes


def find_available_volumes(id_tags, instance_info, filters=()):
    availability_zone = instance_info['Placement']['AvailabilityZone']

    filters = _filters_with_tags(filters, id_tags)
    filters.extend([
        {'Name': 'status', 'Values': ['creating', 'available']},
        {'Name': 'availability-zone', 'Values': [availability_zone]}
    ])

    paginator = ec2().get_paginator('describe_volumes')
    volumes = []
    for response in paginator.paginate(Filters=filters, DryRun=False):
        volumes.extend(response['Volumes'])

    return list(volumes)


def find_existing_snapshot(search_tags, filters=()):
    filters = _filters_with_tags(filters, search_tags)
    filters.append({'Name': 'status', 'Values': ['completed']})

    paginator = ec2().get_paginator('describe_snapshots')
    snapshots = []

    responses = paginator.paginate(Filters=filters,
                                   RestorableByUserIds=[get_account_id()],
                                   DryRun=False)

    for response in responses:
        snapshots.extend(response['Snapshots'])

    try:
        return max(snapshots, key=lambda snap: snap['StartTime'])
    except ValueError:
        return None


def create_volume(id_tags, extra_tags, availability_zone, volume_type,
                  size, iops=None, kms_key_id=None, src_snapshot_id=None):
    extra_tags = extra_tags or []
    tags = [{'Key': k, 'Value': v} for k, v in chain(id_tags, extra_tags)]

    params = {}
    if iops:
        params['Iops'] = iops
    if kms_key_id:
        params['Encrypted'] = True
        params['KmsKeyId'] = kms_key_id
    if src_snapshot_id:
        params['SnapshotId'] = src_snapshot_id
    else:
        params['Size'] = size

    volume = ec2().create_volume(
        AvailabilityZone=availability_zone,
        VolumeType=volume_type,
        TagSpecifications=[{'ResourceType': 'volume', 'Tags': tags}],
        DryRun=False,
        **params
    )

    waiter = ec2().get_waiter('volume_available')
    waiter.wait(VolumeIds=[volume['VolumeId']], DryRun=False)

    return volume


def _parse_dev_name(s):
    num = 0
    for digit, c in enumerate(reversed(s)):
        digit_val = ord(c) - ord('a')
        if digit_val > 25:
            raise ValueError('Bad base26 literal')

        if digit > 0:
            digit_val += 1

        num += (26 ** digit) * digit_val

    return num


def _format_dev_name(n):
    s = ''
    while n >= 0:
        s = chr(ord('a') + n % 26) + s
        n = n // 26 - 1

    return s


def next_device_name(dev):
    match = re.match(r'(/dev/)?(sd|xvd)([a-z]+)', dev)
    if not match:
        raise ValueError('Invalid device name {}'.format(dev))

    # Extract the device ID as a list of numbers from the device name
    path, prefix, dev_name = match.groups()
    dev_index = _parse_dev_name(dev_name)
    return '{}{}{}'.format(path or '', prefix, _format_dev_name(dev_index + 1))


def _is_error_for_device_in_use(exc):
    err = exc.response['Error']
    if err['Code'] != 'InvalidParameterValue':
        return False

    if not re.search(r'(?i)attachment point \S+ is already in use',
                     err['Message']):
        return False

    return True


def attach_volume(volume_id, instance_info, device_name='auto'):
    instance_id = instance_info['InstanceId']

    # Wait until volume is available before attaching it
    waiter = ec2().get_waiter('volume_available')
    waiter.wait(VolumeIds=[volume_id], DryRun=False)

    cur_device = '/dev/sdf' if device_name == 'auto' else device_name
    while True:
        logger.info('Attaching volume %s to instance %s as device %s',
                    volume_id, instance_id, cur_device)
        try:
            ec2().attach_volume(Device=cur_device,
                                InstanceId=instance_id,
                                VolumeId=volume_id,
                                DryRun=False)
        except ClientError as e:
            if not _is_error_for_device_in_use(e):
                raise

            logger.info('Selected device name is already in use, trying again '
                        'with the next one')
            cur_device = next_device_name(cur_device)
        else:
            break

    # Wait until attachment finishes
    waiter = ec2().get_waiter('volume_in_use')
    waiter.wait(
        VolumeIds=[volume_id],
        Filters=[{'Name': 'attachment.status', 'Values': ['attached']}],
        DryRun=False)

    return cur_device
