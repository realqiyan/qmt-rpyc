"""Client profile storage and credential resolution."""

import os
import stat
import uuid
from pathlib import Path

from platformdirs import user_config_dir

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.9-3.10
    import tomli as tomllib

import tomli_w


KEYRING_SERVICE = "qmt-rpyc"
DEFAULT_PROFILE = "default"


def client_config_path():
    override = os.environ.get("QMT_RPYC_CLIENT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir("qmt-rpyc")) / "client.toml"


def load_profiles(path=None):
    config_path = Path(path) if path else client_config_path()
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fp:
        data = tomllib.load(fp)
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("client config [profiles] must be a table")
    return profiles


def save_profiles(profiles, path=None):
    config_path = Path(path) if path else client_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    temp_path.write_text(
        tomli_w.dumps({"profiles": profiles}), encoding="utf-8"
    )
    try:
        temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    temp_path.replace(config_path)
    return config_path


def _keyring_module():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def probe_keyring():
    keyring = _keyring_module()
    if keyring is None:
        return False, "keyring package is not installed"
    username = "_probe_{}".format(uuid.uuid4().hex)
    secret = uuid.uuid4().hex
    try:
        keyring.set_password(KEYRING_SERVICE, username, secret)
        if keyring.get_password(KEYRING_SERVICE, username) != secret:
            return False, "credential round-trip returned a different value"
        keyring.delete_password(KEYRING_SERVICE, username)
        return True, ""
    except Exception as e:
        try:
            keyring.delete_password(KEYRING_SERVICE, username)
        except Exception:
            pass
        return False, "{}: {}".format(type(e).__name__, e)


def set_profile_secret(profile, secret):
    keyring = _keyring_module()
    if keyring is None:
        raise RuntimeError("keyring package is not installed")
    keyring.set_password(KEYRING_SERVICE, profile, secret)


def get_profile_secret(profile):
    keyring = _keyring_module()
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, profile)
    except Exception:
        return None


def delete_profile_secret(profile):
    keyring = _keyring_module()
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, profile)
    except Exception:
        pass


def save_profile(name, values, secret=None, store_plaintext=False, path=None):
    profiles = load_profiles(path)
    profiles[name] = {
        key: value for key, value in values.items()
        if value is not None and key != "auth_key"
    }
    if secret:
        if store_plaintext:
            profiles[name]["auth_key"] = secret
            profiles[name]["secret_store"] = "config"
        else:
            set_profile_secret(name, secret)
            profiles[name]["secret_store"] = "keyring"
    return save_profiles(profiles, path)


def delete_profile(name, path=None):
    profiles = load_profiles(path)
    existed = profiles.pop(name, None) is not None
    if existed:
        save_profiles(profiles, path)
    delete_profile_secret(name)
    return existed


def resolve_profile(name=DEFAULT_PROFILE, overrides=None, path=None):
    overrides = overrides or {}
    profiles = load_profiles(path)
    if name not in profiles:
        raise KeyError("profile {!r} does not exist".format(name))
    profile = dict(profiles[name])

    env_values = {
        "host": os.environ.get("QMT_RPYC_HOST"),
        "port": os.environ.get("QMT_RPYC_PORT"),
        "auth_key": os.environ.get("QMT_RPYC_AUTH_KEY"),
        "timeout": os.environ.get("QMT_RPYC_TIMEOUT"),
        "ca_certs": os.environ.get("QMT_RPYC_TLS_CA"),
        "certfile": os.environ.get("QMT_RPYC_TLS_CERT"),
        "keyfile": os.environ.get("QMT_RPYC_TLS_KEY"),
    }
    for key, value in env_values.items():
        if value not in (None, ""):
            profile[key] = value
    for key, value in overrides.items():
        if value is not None:
            profile[key] = value

    if not profile.get("auth_key"):
        profile["auth_key"] = get_profile_secret(name)
    profile["host"] = profile.get("host", "127.0.0.1")
    profile["port"] = int(profile.get("port", 18812))
    profile["timeout"] = float(profile.get("timeout", 30))
    return profile
