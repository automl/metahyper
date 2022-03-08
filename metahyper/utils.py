from __future__ import annotations

from pathlib import Path
from abc import abstractmethod
import json
import inspect
from typing import Any

import dill

# Serializers

class DataSerializer:
    SUFFIX = ""
    @abstractmethod
    def load(self, path: Path | str):
        raise NotImplementedError

    @abstractmethod
    def dump(self, data: Any, path: Path | str):
        raise NotImplementedError

class DillSerializer(DataSerializer):
    SUFFIX = ".dill"
    def load(self, path: Path | str):
        with open(str(path), "rb") as file_stream:
            return dill.load(file_stream)

    def dump(self, data: Any, path: Path | str):
        with open(str(path), "wb") as file_stream:
            return dill.dump(data, file_stream)

class JsonSerializer(DataSerializer):
    SUFFIX = ".json"
    def load(self, path: Path | str):
        with open(str(path), "r") as file_stream:
            return json.load(file_stream)

    def dump(self, data: Any, path: Path | str):
        with open(str(path), "w") as file_stream:
            return json.dump(data, file_stream)

SerializerMapping = {
    "json": JsonSerializer,
    "dill": DillSerializer,
}

# Mappings

def instanceFromMap(mapping: dict[str, Any], request: str | Any, name="mapping", allow_any=True):
    """Get an instance of an class from a mapping.

    Arguments:
        mapping: Mapping from string keys to classes or instances
        request: A key from the mapping. If allow_any is True, could also be an
            object or a class, to use a custom object.
        name: Name of the mapping used in error messages
        allow_any: if set to True, allows using custom classes/objects.

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

    if inspect.isclass(instance):
        instance = instance()
    return instance
