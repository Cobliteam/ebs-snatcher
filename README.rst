ebs-snatcher
============

Automatically provision and attach AWS EBS volumes to instances


Installation
------------

Run ``pip install ebs-snatcher``, or ``python ./setup.py``


Purpose
-------

This tool allows AWS instances to self-sufficiently provision or takeover EBS
volumes for data storage. It was created to support stateful database instances
that should be able to join a replication group autonomously, while making use
of snapshots to start from a recent state, instead of scratch.


Process
-------

``ebs-snatcher`` will attempt to provision an EBS volume with the minimal
possible effort for a target instance, considering that both must reside in
the same availability zone. Suitable volumes and snapshots are identified by
tag key-value pairs, such that different types of volumes can be managed by
calling the program multiple times.

In order of preference, the choices are:

1. Keep using any volumes already attached to the instance
2. Find an existing but available volume in the same AZ as the instance, and
   attach it to the instance
3. Create a new volume from a suitable snapshot, and attach it
4. Create a new volume from scratch, and attach it


Identifying volumes and snapshots
---------------------------------

Volumes and snapshots will only be considered for usage if they match the set
of tags specified by the ``--volume-id-tag``  and ``--snapshot-search-tag``
arguments, respectively. They take the form of ``key=value`` pairs, and can
be specified multiple times, in which case the conditions are combined in AND
fashion.

Extra tags that are not used for selection can be specified with
``--volume-extra-tags`` (also in ``key=value`` format).


Attachment device selection
---------------------------

After a volume is provisioned, it needs to be attached to the instance and
assigned a device name in the operating system. The desired name can be
specified with the ``--attach-device`` argument, or it can be set to ``auto``
to start from the AWS default of ``/dev/sdf`` (earlier devices are "reserved"
for instance-store volumes).

If an attachment attempt fails due to the name already being in use, and it
matches known Linux disk names (``/dev/(sd|xvd)[a-z]+``), the next name in
order will be picked and retried, in alphabetical order. The sequence starts as:

``sda, sdb, ..., sdz, sdaa, ..., sdaz, sdba, ...``


Volume creation
---------------

Volume creation settings are exposed as command line arguments, corresponding to
the AWS API parameters, with a few observations:

1. The `Encrypt` and `KMSKeyId` options are combined into the
   ``--encrypt-kms-key-id`` argument. Providing it will enable encryption, and
   omitting it will disable it
2. Volume size will be silently ignored when creating from a snapshot, as the
   volume will always have the same size as the snapshot


Output
------

When no errors are found, the command will exit with status 0, and print a JSON
document in stdout, containing at least the following keys:

- ``volume_id``: ID of the existing/attached/created EBS volume
- ``attached_device``: path of the device assigned to the EBS volume

In case of errors, the exit status will be non-zero, and the contents of stdout
are unspecified.

In both cases log messages are printed to stderr.


IAM Permissions
---------------

Permissions to list volumes, snapshots and instances, to create volumes and
to attach volumes to instances are necessary for ``ebs-snatcher`` to work.
They should be granted to instances using IAM roles. The simplest policy allows
complete access to all of those actions. It is much easier to handle, but very
broad:

.. code:: json

    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EBSSnatcherAll",
                "Action": [
                    "ec2:DescribeInstances",
                    "ec2:DescribeSnapshots",
                    "ec2:DescribeVolumes",
                    "ec2:CreateVolume",
                    "ec2:AttachVolume"
                ],
                "Effect": "Allow",
                "Resource": "*"
            }
      ]
    }


It is possible to ensure all managed volumes and instances have some chosen
tags, as long as those same tags are passed to ``ebs-snatcher``.
An example policy to achieve it follows. Before using it, replace:

- ``db-volume-type`` and ``cassandra`` with the desired tags for volumes
- ``db-instance-type`` and ``cassandra`` with the desired tags for instances
  (which must have been already assigned)
- ``<REGION>`` and ``<ACCOUNT-ID>`` with your own AWS account information

.. code:: json

    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EBSSnatcherDescribe",
                "Action": [
                    "ec2:DescribeInstances",
                    "ec2:DescribeSnapshots",
                    "ec2:DescribeVolumes"
                ],
                "Effect": "Allow",
                "Resource": "*"
            },
            {
                "Sid": "EBSSnatcherCreateVolume",
                "Action": [
                    "ec2:CreateVolume"
                ],
                "Condition": {
                    "StringEquals": {
                        "aws:RequestTag/db-volume-type": "cassandra"
                    }
                },
                "Effect": "Allow",
                "Resource": "*"
            },
            {
                "Sid": "EBSSnatcherCreateVolumeTags",
                "Action": [
                    "ec2:CreateTags"
                ],
                "Condition": {
                    "StringEquals": {
                        "ec2:CreateAction": "CreateVolume"
                    }
                },
                "Effect": "Allow",
                "Resource": "arn:aws:ec2:<REGION>:<ACCOUNT-ID>:volume/*"
            },
            {
                "Sid": "EBSSnatcherAttachVolumeVolume",
                "Action": [
                    "ec2:AttachVolume"
                ],
                "Condition": {
                    "StringEquals": {
                        "ec2:ResourceTag/db-volume-type": "cassandra"
                    }
                },
                "Effect": "Allow",
                "Resource": "arn:aws:ec2:<REGION>:<ACCOUNT-ID>:volume/*"
            },
            {
                "Sid": "EBSSnatcherAttachVolumeInstance",
                "Action": [
                    "ec2:AttachVolume"
                ],
                "Effect": "Allow",
                "Condition": {
                    "StringEquals": {
                        "ec2:ResourceTag/db-instance-type": "cassandra"
                    }
                },
                "Resource": "arn:aws:ec2:<REGION>:<ACCOUNT-ID>:instance/*"
            }
        ]
    }


Future enhancements
-------------------

- Generate minimal IAM policies programatically
- Pull instance information from the local metadata store (remove the need for
  DescribeInstances permissions)
- 

Usage
-----

::

    usage: ebs-snatcher [-h] --instance-id ID --volume-id-tag KEY=VALUE
                        --volume-size GB --snapshot-search-tag KEY=VALUE
                        --attach-device PATH|auto [--volume-extra-tag KEY=VALUE]
                        [--encrypt-kms-key-id KEY-ID] [--volume-type TYPE]
                        [--volume-iops COUNT]

    Automatically provision AWS EBS volumes from snapshots

    optional arguments:
      -h, --help            show this help message and exit
      --instance-id ID      Instance ID to attach volumes to
      --volume-id-tag KEY=VALUE
                            Tag used to identify desired volumes. Will be used to
                            search currently attached volumes to determine if a
                            new one is needed and applied to new volumes. Can be
                            provided multiple times, in which case tags will be
                            combined as an AND condition.
      --volume-size GB      Size to assign to newly created volumes, in GBs.
      --snapshot-search-tag KEY=VALUE
                            Tag used to identify snapshots to create new volumes
                            from.Can be provided multiple times, in which case
                            tags will be combined as an AND condition.
      --attach-device PATH|auto
                            Name of device to use when attaching a volume, such as
                            "/dev/sdb". Can also be set to "auto", in which case
                            sequential device names starting from /dev/sdb will be
                            tried until attaching succeeeds
      --volume-extra-tag KEY=VALUE
                            Extra tags to be applied to newly create volumes, but
                            which are not used for identification .
      --encrypt-kms-key-id KEY-ID
                            Enable encryption and use the given KMS key ID for
                            newly created volumes
      --volume-type TYPE    Volume type to use for newly created volumes
      --volume-iops COUNT   Number of provisioned I/O operations to assign to
                            newly created volumes. Make sure to choose an
                            appropriate volume type to match.


License (MIT)
-------------

::

    Copyright (C) 2017 Cobli

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
