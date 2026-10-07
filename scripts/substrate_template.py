"""Print the Moyai ActorTemplate for `kubectl ate create actor-template -f -`."""
import argparse
import json
import re


def template(*, image, storage, public_key, atespace='moyai', name='moyai', memory='4Gi', workload='moyai'):
    if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', image):
        raise ValueError('Substrate requires an image digest: registry/image@sha256:<digest>. Inspect the pushed image to get its RepoDigest.')
    return {
        'metadata': {'atespace': atespace, 'name': name},
        'workerSelector': {'matchLabels': {'workload': workload}},
        'containers': [{'name': 'workspace', 'image': image,
                        'env': [{'name': 'MOYAI_SUBSTRATE_PUBLIC_KEY', 'value': public_key}],
                        'volumeMounts': [{'name': 'identity', 'mountPath': '/run/moyai'}],
                        'wakeupProbe': {'httpGet': {'path': '/health', 'port': 80}}}],
        'volumes': [{'name': 'identity', 'systemInfo': {'dataSources': [{'actorMetadata': {
            'items': [{'field': 'ACTOR_METADATA_FIELD_UID', 'path': 'uid'}]}}]}}],
        'resources': {'limits': [{'name': 'cpu', 'quantity': '2'}, {'name': 'memory', 'quantity': memory}]},
        'snapshotConfig': {'storageLocation': storage, 'onPause': 'SNAPSHOT_CONTENT_SCOPE_FULL',
                           'onCommit': 'SNAPSHOT_CONTENT_SCOPE_FULL'},
        'sandboxConfig': {'sandboxClass': 'SANDBOX_CLASS_GVISOR', 'configName': 'gvisor-default'},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--storage', required=True)
    parser.add_argument('--public-key', required=True)
    parser.add_argument('--atespace', default='moyai')
    parser.add_argument('--name', default='moyai')
    parser.add_argument('--memory', default='4Gi')
    parser.add_argument('--workload', default='moyai')
    print(json.dumps(template(**vars(parser.parse_args())), indent=2))
