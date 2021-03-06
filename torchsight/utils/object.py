"""A module with utilities to work with json objects."""
import json

from .merge import merge_dicts


class JsonObject():
    """A class that allows to transform a dict into an object to make
    less verbose access to the properties of the dict.

    Is very useful when you are working with very nested dicts or if you want to
    deeply merge two dicts and keep working.

    If your are using a linter like pylint you can found some errors
    in your code telling that "Instance of '<this class>' has no '<attribute' member."
    You can ignore this class adding to your .pylintrc the following text:
    [TYPECHECK]
    ignored-classes=JsonObject,OtherClass,AnotherClass
    """

    def __init__(self, data):
        """Initialize the object.

        Arguments:
            data (dict or str): The dict to be transformed to object or a path
                to a json file.
        """
        if isinstance(data, str):
            with open(data, 'r') as file:
                data = json.loads(file.read())

        for key, value in data.items():
            setattr(self, key, self._transform(value))

    def _transform(self, value):
        """Transform the given value to set it as attribute.

        If the value has more values inside (like a list or another dict) it transforms it
        recursive.

        Arguments:
            value: The value to transform recursively or not.

        Returns:
            The transformed value.
        """
        if isinstance(value, (tuple, list, set, frozenset)):
            return type(value)([self._transform(v) for v in value])

        if isinstance(value, dict):
            return self.__class__(value)

        return value

    def _to_dict(self, item):
        """Deeply transform to dict the given item.

        Returns:
            dict: with no values as JsonObject.
        """
        if not isinstance(item, (self.__class__, dict, list, tuple)):
            return item

        # If the item is a dict
        if isinstance(item, dict):
            for key, value in item.items():
                item[key] = self._to_dict(value)
            return item

        # if the item is an iterable
        if isinstance(item, (list, tuple)):
            return [self._to_dict(i) for i in item]

        # If the item is a JsonObject
        original = {}
        for key, value in item.items():
            original[key] = self._to_dict(value)
        return original

    def dict(self):
        """Get the original dict based on the __dict__ of this instance.

        Returns:
            dict: The original dict of this instance.
        """
        return self._to_dict(self)

    def __str__(self):
        """The human readable version of the object.

        Returns:
            str: The string representing this instance.
        """
        return json.dumps(self.dict(), indent=2)

    def merge(self, data, verbose=False):
        """Deep merge the current object with another dict or JsonObject instance.

        If the actual data collides with the new data, the new data take precedence, recursively.

        Arguments:
            data (dict or JsonObject): The new data to merge with the actual data.
            verbose (bool, optional): If True it will print a message when adding/updating values.

        Returns:
            JsonObject: The self instance.
        """
        if isinstance(data, self.__class__):
            data = data.dict()

        data = merge_dicts(self.dict(), data, verbose=verbose)

        self.__class__.__init__(self, data)

        return self

    def keys(self):
        """Get the keys of the instance.

        Returns:
            dict_keys: The list with the keys of this instance.
        """
        return self.__dict__.keys()

    def values(self):
        """Get the values of the instance.

        Returns:
            dict_values: the values of the instance.
        """
        return self.__dict__.values()

    def items(self):
        """Get the list of (key, value) pairs of the instance.

        Returns:
            list of tuple: with the key, value pairs.
        """
        return self.__dict__.items()

    def __getitem__(self, key):
        """Get an item of the object by key.

        This method allows to make the object subscriptable and make things like **object.

        Arguments:
            key: The key of the value to get.

        Returns:
            The value for the given key.
        """
        try:
            return getattr(self, str(key))
        except AttributeError:
            raise KeyError()

    def __setitem__(self, key, value):
        """Set a new property in this object.

        Arguments:
            key: the key to identify the property.
            value: the value to set to the given key.
        """
        setattr(self, str(key), self._transform(value))

    def __contains__(self, key):
        """Check if this JsonObject contains a given key.

        Useful for the `in` operator.

        Arguments:
            key: the key to check if exists in this instance.

        Returns:
            bool: indicating if this instance contains the given key.
        """
        try:
            self.__getitem__(key)
            return True
        except KeyError:
            return False

    def pop(self, key):
        """Remove the attribute named with the given key and returns its value.

        Arguments:
            key (str): The name of the attribute to delete.

        Returns:
            *: the value that the attribute had.
        """
        value = self[key]
        delattr(self, key)
        return value
