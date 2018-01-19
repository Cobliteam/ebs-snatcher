from __future__ import unicode_literals
from builtins import str, bytes

import argparse
import json
import logging
import random

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
             '"/dev/sdb". Can be set to "auto" to use a safe default. '
             'Device names found to be already in use will be skipped, and the '
             'next name in alphabetical order will be tried until attachment '
             'succeeds')
    argp.add_argument(
        '--volume-extra-tag', metavar='KEY=VALUE', type=key_tag_pair,
        action='append',
        help='Extra tags to be applied to newly create volumes, but which are '
             'not used for identification')
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
    argp.add_argument(
        '--move-to-current-az', action='store_true', default=False,
        help="If there is a volume available in a different AZ than the "
             "current one, instead of skipping it and looking for snapshots "
             "by tag, try to move it to the current AZ, by cloning it and "
             "deleting the original.")

    return argp.parse_args()


def positive_int(s):
    n = int(s)
    if n <= 0:
        raise ValueError('Value must be positive: {}'.format(n))

    return n


def key_tag_pair(s):
    if isinstance(s, bytes):
        s = str(s, 'utf-8')
    elif not isinstance(s, str):
        raise TypeError('Input must be a string')

    try:
        key, value = s.split('=', 1)
    except ValueError:
        raise ValueError('Missing tag value: {}'.format(s))

    return key, value


class ResourceState(object):
    def __init__(self, args, instance_info):
        self.args = args
        self.instance_info = instance_info

        self.state = None
        self.volume_id = None
        self.old_volume_id = None
        self.snapshot_id = None
        self.attached_device = None

    def survey(self):
        logger.debug('Looking up currently attached volumes')

        attached_volumes = \
            ebs.find_attached_volumes(self.args.volume_id_tag,
                                      self.instance_info)
        if attached_volumes:
            volume_id = attached_volumes[0]['VolumeId']
            attached_device = attached_volumes[0]['Attachments'][0]['Device']
            logger.info(
                'Found volume already attached to instance: %s', volume_id)

            self.state = 'present'
            self.volume_id = volume_id
            self.attached_device = attached_device
            return

        logger.debug('Looking up existing available volumes in AZ')

        volumes = \
            ebs.find_available_volumes(self.args.volume_id_tag,
                                       self.instance_info, current_az=True)
        if volumes:
            logger.info(
                'Found available volumes with given specifications in current '
                'AZ: %s',
                ', '.join(map(lambda v: v['VolumeId'], volumes)))

            self.state = 'attached'
            self.volume_id = random.choice(volumes)['VolumeId']
            return

        if self.args.move_to_current_az:
            logger.info('Did not find any available volumes in current AZ. '
                        'Searching for available volumes to move in other AZ')

            other_az_volumes = \
                ebs.find_available_volumes(self.args.volume_id_tag,
                                           self.instance_info,
                                           current_az=False)
            for old_volume in other_az_volumes:
                old_volume_id = old_volume['VolumeId']
                old_az = old_volume['AvailabilityZone']
                new_az = self.instance_info['Placement']['AvailabilityZone']

                filters = [{'Name': 'volume-id', 'Values': [old_volume_id]}]
                snapshot = ebs.find_existing_snapshot(filters=filters)

                if snapshot:
                    snapshot_id = snapshot['SnapshotId']
                    logger.info(
                        'Found volume %s in AZ %s, will attempt to move '
                        'it to current AZ %s. Using snapshot %s.',
                        old_volume_id, old_az, new_az, snapshot_id)

                    self.state = 'created'
                    self.snapshot_id = snapshot_id
                    self.old_volume_id = old_volume_id
                    break
            else:
                logger.info('Did not find any available volumes in other AZ '
                            'move. Creating new volume from scratch.')
                self.state = 'created'
        else:
            logger.info('Did not find any available volumes. Searching for a '
                        'suitable snapshot instead')

            snapshot = ebs.find_existing_snapshot(
                search_tags=self.args.snapshot_search_tag)
            self.state = 'created'
            self.snapshot_id = snapshot and snapshot['SnapshotId']

    def converge(self):
        if not self.volume_id:
            availability_zone = \
                self.instance_info['Placement']['AvailabilityZone']
            logger.info('About to create volume in AZ %s', availability_zone)

            if not self.snapshot_id:
                logger.info('Creating volume from scratch')
            else:
                logger.info('Creating volume from snapshot %s',
                            self.snapshot_id)

            new_volume = ebs.create_volume(
                id_tags=self.args.volume_id_tag,
                extra_tags=self.args.volume_extra_tag,
                availability_zone=availability_zone,
                volume_type=self.args.volume_type,
                size=self.args.volume_size,
                iops=self.args.volume_iops,
                kms_key_id=self.args.encrypt_kms_key_id,
                src_snapshot_id=self.snapshot_id)

            self.volume_id = new_volume['VolumeId']

        if not self.attached_device:
            self.attached_device = ebs.attach_volume(
                volume_id=self.volume_id,
                instance_info=self.instance_info,
                device_name=self.args.attach_device)

        self.attached_device = \
            ebs.find_system_block_device(self.volume_id, self.attached_device)

        if self.old_volume_id:
            ebs.delete_volume(volume_id=self.old_volume_id)

    def to_json(self):
        return {'volume_id': self.volume_id,
                'attached_device': self.attached_device,
                'result': self.state,
                'src_snapshot_id': self.snapshot_id}


def main():
    logging.basicConfig(level=logging.DEBUG)

    args = get_args()

    instance_info = ebs.get_instance_info(args.instance_id)

    resource_state = ResourceState(args, instance_info)
    resource_state.survey()
    resource_state.converge()

    print(json.dumps(resource_state.to_json()))
    return 0


if __name__ == '__main__':
    main()  # pragma: no cover
