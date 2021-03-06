import os
import json
import shutil
import logging
import logging.config
import hashlib
from datetime import datetime
import argparse
from copy import deepcopy
from itertools import chain
from collections import OrderedDict
from baseline.utils import export, str2bool, read_config_file, write_json, get_logging_level

__all__ = []
exporter = export(__all__)
logger = logging.getLogger('mead')


@exporter
def configure_logger(logger_config):
    """Use the logger file (logging.json) to configure the log, but overwrite the filename to include the PID

    There are reporting and timing loggers that are configured, the latter being used for speed testing.

    :param logger_config: The logging configuration JSON or file containing JSON
    :return: A dictionary config derived from the logger_file, with the reporting handler suffixed with PID
    """

    config = read_config_file_or_json(logger_config, 'logger')
    config['handlers']['reporting_file_handler']['filename'] = 'reporting-{}.log'.format(os.getpid())
    config['handlers']['timing_file_handler']['filename'] = 'timing-{}.log'.format(os.getpid())
    level = os.getenv('LOG_LEVEL', 'INFO')
    config['loggers']['baseline']['level'] = get_logging_level(os.getenv('BASELINE_LOG_LEVEL', level))
    config['loggers']['mead']['level'] = get_logging_level(os.getenv('MEAD_LOG_LEVEL', level))
    config['handlers']['reporting_console_handler']['level'] = get_logging_level(os.getenv('REPORTING_LOG_LEVEL', level))
    logging.config.dictConfig(config)


@exporter
def print_dataset_info(dataset):
    logger.info("[train file]: {}".format(dataset['train_file']))
    logger.info("[valid file]: {}".format(dataset['valid_file']))
    logger.info("[test file]: {}".format(dataset['test_file']))
    vocab_file = dataset.get('vocab_file')
    if vocab_file is not None:
        logger.info("[vocab file]: {}".format(vocab_file))
    label_file = dataset.get('label_file')
    if label_file is not None:
        logger.info("[label file]: {}".format(label_file))


@exporter
def read_config_file_or_json(config, name=''):
    if isinstance(config, (dict, list)):
        return config
    config = os.path.expanduser(config)
    if os.path.exists(config):
        return read_config_file(config)
    raise Exception('Expected {} config file or a JSON object.'.format(name))


def parse_date(s):
    KNOWN_FMTS = ['%Y%m%d', '%Y-%m-%d', '%Y/%m/%d', '%Y', '%Y%m', '%Y-%m', '%Y/%m']
    for fmt in KNOWN_FMTS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt
        except:
            continue
    raise Exception("Couldn't parse datestamp {}".format(s))

@exporter
def get_dataset_from_key(dataset_key, datasets_set):

    # This is the previous behavior
    if dataset_key in datasets_set:
        return datasets_set[dataset_key]

    last_date = parse_date('1900')
    last_k = None
    for k, v in datasets_set.items():

        if dataset_key in k:
            dt = parse_date(k.split(':')[-1])
            if dt > last_date:
                last_date = dt
                last_k = k

    if last_k is None:
        raise Exception("No dataset could be found with key {}".format(dataset_key))

    return datasets_set[last_k]


@exporter
def get_mead_settings(mead_settings_config):
    if mead_settings_config is None:
        return {}
    return read_config_file_or_json(mead_settings_config, 'mead settings')


@exporter
def index_by_label(object_list):
    objects = {x['label']: x for x in object_list}
    return objects


@exporter
def convert_path(path, loc=None):
    """If the provided path doesn't exist search for it relative to loc (or this file)."""
    if os.path.isfile(path):
        return path
    if path.startswith("$"):
        return path
    if loc is None:
        loc = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(loc, path)


def _infer_type_or_str(x):
    try:
        return str2bool(x)
    except:
        try:
            return float(x)
        except ValueError:
            return x


@exporter
def parse_extra_args(base_args, extra_args):
    """Parse extra command line arguments based on based names.
    Note:
        special args should be in the form --{base_name}:{special_name}
    :param base_args: List[str], A list of base argument names.
    :param extra_args: List[str], A list of special arguments and values.
    :returns:
        dict, The parsed special settings in the form
        {
            "base": {
                "special": val,
                ...
            },
            ...
        }
    """
    found_args = []
    for arg in base_args:
        key = "{}:".format(arg)
        for extra_arg in extra_args:
            if key in extra_arg:
                found_args.append(extra_arg)
    parser = argparse.ArgumentParser()
    for key in found_args:
        parser.add_argument(key, type=_infer_type_or_str)
    args = parser.parse_known_args(extra_args)[0]
    settings = {arg: {} for arg in base_args}
    args = vars(args)
    for key in args:
        base, extra = key.split(":")
        settings[base][extra] = args[key]
    return settings


@exporter
def order_json(data):
    """Sort json to a consistent order.
    When you hash json that has the some content but is different orders you get
    different fingerprints.
    In:  hashlib.sha1(json.dumps({'a': 12, 'b':14}).encode('utf-8')).hexdigest()
    Out: '647aa7508f72ece3f8b9df986a206d95fd9a2caf'
    In:  hashlib.sha1(json.dumps({'b': 14, 'a':12}).encode('utf-8')).hexdigest()
    Out: 'a22215982dc0e53617be08de7ba9f1a80d232b23'
    This function sorts json by key so that hashes are consistent.
    Note:
        In our configs we only have lists where the order doesn't matter so we
        can sort them for consistency. This would have to change if we add a
        config field that needs order we will need to refactor this.
    :param data: dict, The json data.
    :returns:
        collections.OrderedDict: The data in a consistent order (keys sorted alphabetically).
    """
    new = OrderedDict()
    for (key, value) in sorted(data.items(), key=lambda x: x[0]):
        if isinstance(value, dict):
            value = order_json(value)
        new[key] = value
    return new


KEYS = {
    ('conll_output',),
    ('visdom',),
    ('visdom_name',),
    ('model', 'gpus'),
    ('test_thresh',),
    ('reporting',),
    ('num_valid_to_show',),
    ('train', 'verbose'),
    ('train', 'model_base'),
    ('train', 'model_zip'),
    ('train', 'nsteps'),
    ('test_batchsz'),
    ('basedir'),
}


@exporter
def remove_extra_keys(config, keys=KEYS):
    """Remove config items that don't effect the model.
    We base most things off of the sha1 hash of the model configs but there
    is a problem. Some things in the config file don't effect the model such
    as the name of the `conll_output` file or if you are using `visdom`
    reporting. This strips out these kind of things so that as long as the model
    parameters match the sha1 will too.
    :param config: dict, The json data.
    :param keys: Set[Tuple[str]], The keys to remove.
    :returns:
        dict, The config with certain keys removed.
    """
    c = deepcopy(config)
    for key in keys:
        x = c
        for k in key[:-1]:
            x = x.get(k)
            if x is None:
                break
        else:
            _ = x.pop(key[-1], None)
    return c


@exporter
def hash_config(config):
    """Hash a json config with sha1.
    :param config: dict, The config to hash.
    :returns:
        str, The sha1 hash.
    """
    stripped_config = remove_extra_keys(config)
    sorted_config = order_json(stripped_config)
    json_bytes = json.dumps(sorted_config).encode('utf-8')
    return hashlib.sha1(json_bytes).hexdigest()


def _listdir(model_dir):
    try:
        return os.listdir(model_dir)
    except OSError:
        return []


@exporter
def find_model_version(model_dir):
    """Find the next usable model version when exporting.

    :param model_dir: `str` The directory we are exporting to.

    :returns: `str` The model version.
    """
    return str(max(chain([0], map(int, filter(lambda x: x.isdigit(), _listdir(model_dir))))) + 1)


@exporter
def get_output_paths(
        output_dir,
        project=None, name=None,
        version=None,
        remote=False,
        make_server=True
):
    """Create the output paths for export.

    Old behavior:
    if remote == True:
        f"{output_dir}/client/{basename(output_dir)}/{version}"
        f"{output_dir}/server/{basename(output_dir)}/{version}"
    else:
        f"{output_dir}/{version}"

    New Behavior:
    if remote == True:
        f"{output_dir}/client/{project}/{name}/{version}"
        f"{output_dir}/server/{project}/{name}/{version}"
    else:
        f"{output_dir}/{project}/{name}/{version}"

    If either project or name is None then they are skipped in the path.

    If you want to create output that looks the same as the old versions
    then set both project and name to None. This will skip them if remote is
    false or it will add the basename of output_dir to the path if remote.

    :param output_dir: `str` The base of these paths.
    :param project: `str` The first subdir in the created path.
    :param name: `str` The second subdir in the created path.
    :param version: `str` The model version.
    :param remote: `bool` Should we create separate client and server bundles?
    :param maker_server: `bool` When false don't actually make the directory that the
        server part will go in. This is because TF really wants to make it itself.

    :returns: `Tuple[str, str]` The client and server output dirs.
    """
    # In this case we use the basename to simulate the old behavior.
    if remote and project is None and name is None:
        project = os.path.basename(output_dir)
    project = project if project is not None else ''
    name = name if name is not None else ''
    client = 'client' if remote else ''
    server = 'server' if remote else ''
    server_path = [output_dir, server, project, name]
    client_path = [output_dir, client, project, name]
    # Sniff the dir and see what version we should use
    version = find_model_version(os.path.join(*server_path)) if version is None else version
    server_path.append(version)
    client_path.append(version)
    server_path = os.path.join(*server_path)
    client_path = os.path.join(*client_path)
    if remote:
        os.makedirs(client_path)
    if make_server:
        os.makedirs(server_path)
    return client_path, server_path


@exporter
def get_export_params(
        config,
        output_dir=None,
        project=None, name=None,
        model_version=None,
        exporter_type=None,
        return_labels=None,
        is_remote=None,
):
    """Combine export parameters from the config file and cli arguments.

    :param config: `dict` The export block of the config.
    :param output_dir: `str` The base of export paths. (defaults to './models')
    :param project: `str` The name of the project this model is for.
    :param name: `str` The name of this model (often the use case for it, `ner`, `intent` etc).
    :param model_version: `str` The version of this model.
    :param exporter_type: `str` The name of the exporter to use (defaults to 'default')
    :param return_labels: `str` Should labels be returned? (defaults to False)
    :param is_remote: `str` Should the bundle be split into client and server dirs.

    :returns: `Tuple[str, str, str, str, str, bool, bool]`
        The output_dir, project, name, model_version, exporter_type, return_labels, and remote
    """
    project = project if project is not None else config.get('project')
    name = name if name is not None else config.get('name')
    output_dir = output_dir if output_dir is not None else config.get('output_dir', './models')
    output_dir = os.path.expanduser(output_dir)
    model_version = model_version if model_version is not None else config.get('model_version')
    exporter_type = exporter_type if exporter_type is not None else config.get('type', config.get('exporter_type', 'default'))
    return_labels = return_labels if return_labels is not None else config.get('return_labels', False)
    return_labels = str2bool(return_labels)
    is_remote = is_remote if is_remote is not None else config.get('is_remote', True)
    is_remote = str2bool(is_remote)
    return output_dir, project, name, model_version, exporter_type, return_labels, is_remote


def create_metadata(inputs, outputs, sig_name, model_name, lengths_key=None, beam=None, return_labels=False, preproc='client'):
    meta = {
        'inputs': inputs,
        'outputs': outputs,
        'signature_name': sig_name,
        'metadata': {
            'exported_model': str(model_name),
            'exported_time': str(datetime.utcnow()),
            'return_labels': return_labels,
            'preproc': preproc,
        }
    }
    if lengths_key:
        meta['lengths_key'] = lengths_key
    if beam:
        meta['beam'] = beam

    return meta


def save_to_bundle(output_path, directory, assets=None):
    """Save files to the exported bundle.

    :vocabs
    :vectorizers
    :labels
    :assets
    :output_path  the bundle output_path. vocabs, vectorizers know how to save themselves.
    """
    for filename in os.listdir(directory):
        if filename.startswith('vocabs') or \
           filename.endswith(".labels") or \
           filename.startswith('vectorizers'):
            shutil.copy(os.path.join(directory, filename), os.path.join(output_path, filename))

    if assets:
        asset_file = os.path.join(output_path, 'model.assets')
        write_json(assets, asset_file)


def create_feature_exporter_field_map(feature_section, default_exporter_field='tokens'):
    feature_exporter_field_map = {}
    for feature_desc in feature_section:
        if feature_desc.get('exporter_field') is None:
            feature_exporter_field_map[feature_desc['name']] = default_exporter_field
        else:
            feature_exporter_field_map[feature_desc['name']] = feature_desc['exporter_field']
    return feature_exporter_field_map
