from __future__ import print_function, unicode_literals

import re
import argparse
import logging
import json
from itertools import chain
from functools import wraps

import boto3
from botocore.exceptions import ClientError


VOLUME_TYPES = set(['standard', 'gp2', 'io1', 'sc1', 'st1'])

logger = logging.getLogger('ebs-snatcher')


def memoize(f):
    sentinel = object()
    value = sentinel

    @wraps(f)
    def memoized(*args, **kwargs):
        nonlocal value
        if value is sentinel:
            value = f(*args, **kwargs)

        return value

    return memoized


ec2 = memoize(lambda: boto3.client('ec2'))
sts = memoize(lambda: boto3.client('sts'))


def positive_int(s):
    n = int(s)
    if n <= 0:
        raise ValueError('Value must be positive: {}'.format(n))

    return n


def key_tag_pair(s):
    if not isinstance(s, str):
        raise TypeError('Input must be a string')

    try:
        key, value = s.split('=', 1)
    except ValueError:
        raise ValueError('Missing tag value: {}'.format(s))

    return key, value


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


def _next_device(dev):
    match = re.match(r'(/dev/)?(sd|xvd)([a-z]+)', dev)
    if not match:
        raise ValueError('Invalid device name {}'.format(dev))

    # Extract the device ID as a list of numbers from the device name
    path, prefix, dev_id = match.groups()
    dev_id_parts = [ord(c) - ord('a') for c in dev_id]

    # Increment the version by incrementing each element in reverse order. If it
    # overflows, carry to the previous element, until we no longer can
    # (as we reached the start of the list). In that case, add a new element
    # starting from zero. so the order goes something like:
    #
    # sda, sdb, ..., sdz, sdaa, sdab, sdaz, sdba, ... sdzz, sdaaa
    for i in reversed(range(len(dev_id_parts))):
        if dev_id_parts[i] == 25:
            dev_id_parts[i] = 0
            try:
                dev_id_parts[i - 1] += 1
            except IndexError:
                dev_id_parts.append(0)
                break
        else:
            dev_id_parts[i] += 1

    dev_id = ''.join(chr(ord('a') + d) for d in dev_id_parts)
    return '{}{}{}'.format(path, prefix, dev_id)


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

    cur_device = '/dev/sdb' if device_name == 'auto' else device_name
    while True:
        logger.info('Attaching volume %s to instance %s as device %s',
                    volume_id, instance_id, cur_device)
        try:
            ec2().attach_volume(Device=cur_device,
                                InstanceId=instance_id,
                                VolumeId=volume_id,
                                DryRun=False)
        except ClientError as e:
            if device_name != 'auto' or not _is_error_for_device_in_use(e):
                raise

            logger.info('Selected device name is already in use, trying again '
                        'with the next one')
            cur_device = _next_device(cur_device)
        else:
            break

    # Wait until attachment finishes
    waiter = ec2().get_waiter('volume_in_use')
    waiter.wait(
        VolumeIds=[volume_id],
        Filters=[{'Name': 'attachment.status', 'Values': ['attached']}],
        DryRun=False)

    return cur_device


def get_args():
    argp = argparse.ArgumentParser(
        'ebs-snatcher',
        description='Automatically provision AWS EBS volumes from snapshots')
    argp.add_argument(
        '--instance-id', metavar='ID', required=True,
        help='Instance ID to attach volumes to')
    argp.add_argument(
        '--volume-id-tag', metavar='KEY=VALUE', type=key_tag_pair,
        required=True, action='append',
        help='Tag used to identify desired volumes. Will be used to search '
             'currently attached volumes to determine if a new one is needed '
             'and applied to new volumes. Can be provided multiple times, in '
             'which case tags will be combined as an AND condition.')
    argp.add_argument(
        '--volume-size', metavar='GB', type=positive_int, required=True,
        help='Size to assign to newly created volumes, in GBs.')
    argp.add_argument(
        '--snapshot-search-tag', metavar='KEY=VALUE', type=key_tag_pair,
        required=True, action='append',
        help='Tag used to identify snapshots to create new volumes from.'
             'Can be provided multiple times, in which case tags will be '
             'combined as an AND condition.')
    argp.add_argument(
        '--attach-device', metavar='PATH|auto', required=True,
        help='Name of device to use when attaching a volume, such as '
             '"/dev/sdb". Can also be set to "auto", in which case sequential '
             'device names starting from /dev/sdb will be tried until '
             'attaching succeeeds')
    argp.add_argument(
        '--volume-extra-tag', metavar='KEY=VALUE', type=key_tag_pair,
        action='append',
        help='Extra tags to be applied to newly create volumes, but which are '
             'not used for identification .')
    argp.add_argument(
        '--encrypt-kms-key-id', metavar='KEY-ID', default=None,
        help='Enable encryption and use the given KMS key ID for newly created '
             'volumes')
    argp.add_argument(
        '--volume-type', metavar='TYPE', choices=VOLUME_TYPES, default='gp2',
        help='Volume type to use for newly created volumes')
    argp.add_argument(
        '--volume-iops', metavar='COUNT', type=positive_int, default=None,
        help='Number of provisioned I/O operations to assign to newly created '
             'volumes. Make sure to choose an appropriate volume type to '
             'match.')

    return argp.parse_args()


def main():
    logging.basicConfig(level=logging.WARN)
    logger.setLevel(logging.DEBUG)

    args = get_args()

    instance_info = get_instance_info(args.instance_id)

    snapshot_id = None
    attached_device = None
    volume_id = None

    logger.debug('Looking up currently attached volumes')

    attached_volumes = \
        find_attached_volumes(args.volume_id_tag, instance_info)
    if attached_volumes:
        logger.info(
            'Found volume already attached to instance: %s', volume_id)

        volume_id = attached_volumes[0]['VolumeId']
        attached_device = attached_volumes[0]['Attachments'][0]['Device']
    else:
        logger.debug('Looking up existing available volumes in AZ')
        available_volumes = \
            find_available_volumes(args.volume_id_tag, instance_info)
        if available_volumes:
            logger.info(
                'Found available volumes with given specifications in current '
                'AZ: %s',
                ', '.join(map(lambda v: v['VolumeId'], available_volumes)))

            volume_id = available_volumes[0]['VolumeId']
        else:
            logger.info(
                'Did not find any available volumes in current AZ. Searching '
                'for a suitable snapshot instead.')

            snapshot = find_existing_snapshot(args.snapshot_search_tag)
            if snapshot:
                snapshot_id = snapshot['SnapshotId']

    if not volume_id:
        availability_zone = instance_info['Placement']['AvailabilityZone']
        logger.info('About to create volume in AZ %s', availability_zone)

        if not snapshot_id:
            logger.info('Creating volume from scratch')
        else:
            logger.info('Creating volume from snapshot %s', snapshot_id)

        new_volume = create_volume(
            id_tags=args.volume_id_tag,
            extra_tags=args.volume_extra_tag,
            availability_zone=availability_zone,
            volume_type=args.volume_type,
            size=args.volume_size,
            iops=args.volume_iops,
            kms_key_id=args.encrypt_kms_key_id,
            src_snapshot_id=snapshot_id)
        volume_id = new_volume['VolumeId']

    if not attached_device:
        attached_device = attach_volume(volume_id, instance_info,
                                        args.attach_device)

    result = json.dumps({'volume_id': volume_id,
                         'attached_device': attached_device})
    print(result)

    return 0


if __name__ == '__main__':
    main()
