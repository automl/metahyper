from __future__ import annotations

import glob
import inspect
import json
from abc import abstractmethod
from functools import partial
from pathlib import Path
from typing import Any, Callable

import dill
import numpy
import torch
import yaml


def find_files(
    directory: Path, files: list[str], any_suffix=False, check_nonempty=False
) -> list[Path]:
    found_paths = []
    for file_name in files:
        pattern = f"{directory.absolute()}/**/{file_name}"
        if any_suffix:
            pattern += "*"
        for f_path in glob.glob(pattern, recursive=True):
            path_found = Path(f_path)
            if path_found.is_file():
                if check_nonempty and path_found.stat().st_size == 0:
                    continue
                found_paths.append(path_found)
    return found_paths


# Serializers


def get_data_representation(data: Any):
    if isinstance(data, dict):
        return {key: get_data_representation(val) for key, val in data.items()}
    elif isinstance(data, list) or isinstance(data, tuple):
        return [get_data_representation(val) for val in data]
    elif type(data).__module__ == numpy.__name__ or isinstance(data, torch.Tensor):
        return data.tolist()
    elif hasattr(data, "serialize"):
        return data.serialize()
    else:
        return data


class DataSerializer:
    SUFFIX = ""
    PRE_SERIALIZE = True

    def __init__(self, config_loader: Callable | None = None):
        self.config_loader = config_loader or (lambda x: x)

    @abstractmethod
    def _load_from(self, path: str):
        raise NotImplementedError

    @abstractmethod
    def _dump_to(self, data: Any, path: str):
        raise NotImplementedError

    def load(self, path: Path | str, add_suffix=True):
        path = str(path)
        if add_suffix and Path(path).suffix != self.SUFFIX:
            path = path + self.SUFFIX
        return self._load_from(path)

    def dump(self, data: Any, path: Path | str, add_suffix=True):
        if self.PRE_SERIALIZE:
            data = get_data_representation(data)
        path = str(path)
        if add_suffix and Path(path).suffix != self.SUFFIX:
            path = path + self.SUFFIX
        self._dump_to(data, path)

    def load_config(self, path: Path | str):
        if self.PRE_SERIALIZE:
            return self.config_loader(self.load(path))
        return self.load(path)


class DillSerializer(DataSerializer):
    SUFFIX = ".dill"
    PRE_SERIALIZE = False

    def _load_from(self, path: str):
        with open(path, "rb") as file_stream:
            return dill.load(file_stream)

    def _dump_to(self, data: Any, path: str):
        with open(path, "wb") as file_stream:
            return dill.dump(data, file_stream)


class JsonSerializer(DataSerializer):
    SUFFIX = ".json"

    def _load_from(self, path: str):
        with open(path) as file_stream:
            return json.load(file_stream)

    def _dump_to(self, data: Any, path: str):
        with open(path, "w") as file_stream:
            return json.dump(data, file_stream)


class YamlSerializer(DataSerializer):
    SUFFIX = ".yaml"

    def _load_from(self, path: str):
        with open(path) as file_stream:
            return yaml.full_load(file_stream)

    def _dump_to(self, data: Any, path: str):
        with open(path, "w") as file_stream:
            return yaml.dump(data, file_stream)


SerializerMapping = {
    "yaml": YamlSerializer,
    "json": JsonSerializer,
    "dill": DillSerializer,
}

# Mappings


def is_partial_class(obj):
    """Check if the object is a (partial) class, or an instance"""
    if isinstance(obj, partial):
        obj = obj.func
    return inspect.isclass(obj)


def instance_from_map(
    mapping: dict[str, Any],
    request: str | Any,
    name: str = "mapping",
    allow_any: bool = True,
    as_class: bool = False,
    kwargs: dict = None,
):
    """Get an instance of an class from a mapping.

    Arguments:
        mapping: Mapping from string keys to classes or instances
        request: A key from the mapping. If allow_any is True, could also be an
            object or a class, to use a custom object.
        name: Name of the mapping used in error messages
        allow_any: If set to True, allows using custom classes/objects.
        as_class: If the class should be returned without beeing instanciated
        kwargs: Arguments used for the new instance, if created. Its purpose is
            to serve at default arguments if the user doesn't built the object.

    Raises:
        ValueError: if the request is invalid (not a string if allow_any is False),
            or invalid key.
    """

    if isinstance(request, str):
        if request in mapping:
            instance = mapping[request]
        else:
            raise ValueError(f"{request} doesn't exists for {name}")
    elif allow_any:
        instance = request
    else:
        raise ValueError(f"Object {request} invalid key for {name}")

    if as_class:
        if not is_partial_class(instance):
            raise ValueError(f"{instance} is not a class")
        return instance
    if is_partial_class(instance):
        kwargs = kwargs or {}
        try:
            instance = instance(**kwargs)
        except TypeError as e:
            raise TypeError(f"{e} when calling {instance} with {kwargs}") from e
    return instance
