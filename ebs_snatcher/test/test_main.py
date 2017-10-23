from __future__ import unicode_literals

import json

import pytest

from .. import main


@pytest.mark.parametrize('value,result', [
    ('1', 1),
    (b'1', 1),
    ('0', ValueError),
    (b'0', ValueError),
    ('asd', ValueError),
    (b'asd', ValueError),
    (None, TypeError)
])
def test_positive_int(value, result):
    if isinstance(result, type):
        with pytest.raises(result):
            main.positive_int(value)
    else:
        assert main.positive_int(value) == result


@pytest.mark.parametrize('value,result', [
    ('a=b', ('a', 'b')),
    (b'a=b', ('a', 'b')),
    ('a', ValueError),
    (b'a', ValueError),
    ('a=b=c', ('a', 'b=c')),
    (b'a=b=c', ('a', 'b=c')),
    (None, TypeError)
])
def test_key_tag_pair(value, result):
    if isinstance(result, type):
        with pytest.raises(result):
            main.key_tag_pair(value)
    else:
        assert main.key_tag_pair(value) == result


@pytest.fixture
def main_args(mocker, instance_id, attach_device):
    args = mocker.Mock(spec=[
        'instance_id', 'volume_id_tag', 'volume_size', 'snapshot_search_tag',
        'attach_device', 'volume_extra_tag', 'encrypt_kms_key_id',
        'volume_type', 'volume_iops'
    ])

    args.instance_id = instance_id
    args.attach_device = attach_device
    return args


@pytest.fixture
def run_main(capfd, mocker, main_args, instance_info):
    def run_main(**kwargs):
        args = main_args
        for k, v in kwargs.items():
            setattr(args, k, v)
        mocker.patch('ebs_snatcher.main.get_args', return_value=args)

        mocker.patch('ebs_snatcher.ebs.get_instance_info',
                     return_value=instance_info)

        exit_status = main.main()

        out, err = capfd.readouterr()
        json_out = json.loads(out)

        return exit_status, json_out, err

    return run_main


def test_main_already_attached(mocker, run_main, attached_volume, volume_id,
                               attach_device):
    mocker.patch('ebs_snatcher.ebs.find_attached_volumes',
                 return_value=[attached_volume])

    exit_status, json_out, err = run_main()
    assert exit_status == 0
    assert json_out['volume_id'] == volume_id
    assert json_out['attached_device'] == attach_device


def test_main_available_volume(mocker, run_main, volume_id, attach_device,
                               instance_info):
    mocker.patch('ebs_snatcher.ebs.find_attached_volumes',
                 return_value=[])

    volume = {'VolumeId': volume_id}
    mocker.patch('ebs_snatcher.ebs.find_available_volumes',
                 return_value=[volume])

    attach_volume = mocker.patch('ebs_snatcher.ebs.attach_volume',
                                 return_value=attach_device)

    exit_status, json_out, err = run_main()
    assert exit_status == 0
    assert json_out['volume_id'] == volume_id
    assert json_out['attached_device'] == attach_device

    attach_volume.assert_called_once_with(
        volume_id=volume_id,
        instance_info=instance_info,
        device_name=attach_device)


def test_main_available_snapshot(mocker, run_main, main_args, snapshot_id,
                                 volume_id, attach_device, availability_zone,
                                 instance_info):
    mocker.patch('ebs_snatcher.ebs.find_attached_volumes',
                 return_value=[])
    mocker.patch('ebs_snatcher.ebs.find_available_volumes',
                 return_value=[])

    snapshot = {'SnapshotId': snapshot_id}
    mocker.patch('ebs_snatcher.ebs.find_existing_snapshot',
                 return_value=snapshot)

    volume = {'VolumeId': volume_id}
    create_volume = mocker.patch('ebs_snatcher.ebs.create_volume',
                                 autospec=True,
                                 return_value=volume)

    attach_volume = mocker.patch('ebs_snatcher.ebs.attach_volume',
                                 return_value=attach_device)

    exit_status, json_out, err = run_main()
    assert exit_status == 0
    assert json_out['volume_id'] == volume_id
    assert json_out['attached_device'] == attach_device

    create_volume.assert_called_once_with(
        availability_zone=availability_zone,
        src_snapshot_id=snapshot_id,
        id_tags=main_args.volume_id_tag,
        extra_tags=main_args.volume_extra_tag,
        volume_type=main_args.volume_type,
        size=main_args.volume_size,
        iops=main_args.volume_iops,
        kms_key_id=main_args.encrypt_kms_key_id)

    attach_volume.assert_called_once_with(
        volume_id=volume_id,
        instance_info=instance_info,
        device_name=attach_device)


def test_main_create_scratch(mocker, run_main, main_args, snapshot_id,
                             volume_id, attach_device, availability_zone,
                             instance_info):
    mocker.patch('ebs_snatcher.ebs.find_attached_volumes',
                 return_value=[])
    mocker.patch('ebs_snatcher.ebs.find_available_volumes',
                 return_value=[])
    mocker.patch('ebs_snatcher.ebs.find_existing_snapshot',
                 return_value=None)

    volume = {'VolumeId': volume_id}
    create_volume = mocker.patch('ebs_snatcher.ebs.create_volume',
                                 autospec=True,
                                 return_value=volume)

    attach_volume = mocker.patch('ebs_snatcher.ebs.attach_volume',
                                 return_value=attach_device)

    exit_status, json_out, err = run_main()
    assert exit_status == 0
    assert json_out['volume_id'] == volume_id
    assert json_out['attached_device'] == attach_device

    create_volume.assert_called_once_with(
        availability_zone=availability_zone,
        src_snapshot_id=None,
        id_tags=main_args.volume_id_tag,
        extra_tags=main_args.volume_extra_tag,
        volume_type=main_args.volume_type,
        size=main_args.volume_size,
        iops=main_args.volume_iops,
        kms_key_id=main_args.encrypt_kms_key_id)

    attach_volume.assert_called_once_with(
        volume_id=volume_id,
        instance_info=instance_info,
        device_name=attach_device)
