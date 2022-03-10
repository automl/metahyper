from __future__ import annotations

import glob
import inspect
import json
from abc import abstractmethod
from functools import partial
from pathlib import Path
from typing import Any, Callable

import dill
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


class DataSerializer:
    SUFFIX = ""

    def __init__(self, config_loader: Callable | None = None):
        self.config_loader = config_loader or (lambda x: x)

    @abstractmethod
    def load(self, path: Path | str):
        raise NotImplementedError

    def load_config(self, path: Path | str):
        return self.config_loader(self.load(path))

    @abstractmethod
    def dump(self, data: Any, path: Path | str):
        raise NotImplementedError


class DillSerializer(DataSerializer):
    SUFFIX = ".dill"

    def load(self, path: Path | str):
        with open(str(path), "rb") as file_stream:
            return dill.load(file_stream)

    def load_config(self, path: Path | str):
        # The object is already built
        return self.load(path)

    def dump(self, data: Any, path: Path | str):
        with open(str(path), "wb") as file_stream:
            return dill.dump(data, file_stream)


class JsonSerializer(DataSerializer):
    SUFFIX = ".json"

    def load(self, path: Path | str):
        with open(str(path)) as file_stream:
            return json.load(file_stream)

    def dump(self, data: Any, path: Path | str):
        if hasattr(data, "serialize"):
            data = data.serialize()
        with open(str(path), "w") as file_stream:
            return json.dump(data, file_stream)


class YamlSerializer(DataSerializer):
    SUFFIX = ".yaml"

    def load(self, path: Path | str):
        with open(str(path)) as file_stream:
            return yaml.full_load(file_stream)

    def dump(self, data: Any, path: Path | str):
        if hasattr(data, "serialize"):
            data = data.serialize()
        with open(str(path), "w") as file_stream:
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
