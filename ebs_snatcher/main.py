import argparse
import json
import logging

from . import ebs


logger = logging.getLogger('ebs-snatcher.main')


def get_args():  # pragma: no cover
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
        '--volume-type', metavar='TYPE', choices=ebs.VOLUME_TYPES,
        default='gp2',
        help='Volume type to use for newly created volumes')
    argp.add_argument(
        '--volume-iops', metavar='COUNT', type=positive_int, default=None,
        help='Number of provisioned I/O operations to assign to newly created '
             'volumes. Make sure to choose an appropriate volume type to '
             'match.')

    return argp.parse_args()


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


def main():
    logging.basicConfig(level=logging.WARN)
    logger.setLevel(logging.DEBUG)

    args = get_args()

    instance_info = ebs.get_instance_info(args.instance_id)

    snapshot_id = None
    attached_device = None
    volume_id = None

    logger.debug('Looking up currently attached volumes')

    attached_volumes = \
        ebs.find_attached_volumes(args.volume_id_tag, instance_info)
    if attached_volumes:
        volume_id = attached_volumes[0]['VolumeId']
        attached_device = attached_volumes[0]['Attachments'][0]['Device']

        logger.info(
            'Found volume already attached to instance: %s', volume_id)
    else:
        logger.debug('Looking up existing available volumes in AZ')
        available_volumes = \
            ebs.find_available_volumes(args.volume_id_tag, instance_info)
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

            snapshot = ebs.find_existing_snapshot(args.snapshot_search_tag)
            if snapshot:
                snapshot_id = snapshot['SnapshotId']

    if not volume_id:
        availability_zone = instance_info['Placement']['AvailabilityZone']
        logger.info('About to create volume in AZ %s', availability_zone)

        if not snapshot_id:
            logger.info('Creating volume from scratch')
        else:
            logger.info('Creating volume from snapshot %s', snapshot_id)

        new_volume = ebs.create_volume(
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
        attached_device = ebs.attach_volume(
            volume_id=volume_id,
            instance_info=instance_info,
            device_name=args.attach_device)

    result = json.dumps({'volume_id': volume_id,
                         'attached_device': attached_device})
    print(result)

    return 0


if __name__ == '__main__':
    main()  # pragma: no cover
