from __future__ import annotations

from pathlib import Path
from abc import abstractmethod
import json
import inspect
from typing import Any, Callable, Optional

import dill

# Serializers

class DataSerializer:
    SUFFIX = ""
    def __init__(self, config_loader: Optional[Callable] = None):
        self.config_loader = config_loader or (lambda x : x)

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
        with open(str(path), "r") as file_stream:
            return json.load(file_stream)

    def dump(self, data: Any, path: Path | str):
        if hasattr(data, 'serialize'):
            data = data.serialize()
        with open(str(path), "w") as file_stream:
            return json.dump(data, file_stream)

SerializerMapping = {
    "json": JsonSerializer,
    "dill": DillSerializer,
}

# Mappings

def instanceFromMap(mapping: dict[str, Any], request: str | Any, name="mapping", allow_any=True, as_class=False):
    """Get an instance of an class from a mapping.

    Arguments:
        mapping: Mapping from string keys to classes or instances
        request: A key from the mapping. If allow_any is True, could also be an
            object or a class, to use a custom object.
        name: Name of the mapping used in error messages
        allow_any: If set to True, allows using custom classes/objects.
        as_class: If the class should be returned without beeing instanciated

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
        if not inspect.isclass(instance):
            raise ValueError(f"{instance} is not a class")
        return instance
    if inspect.isclass(instance):
        instance = instance()
    return instance
