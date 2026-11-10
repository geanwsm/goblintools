import threading

_STATE_KEY = "_thread_local_state"


class ThreadLocalAttribute:
    def __init__(self, default_factory):
        self.default_factory = default_factory
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    @staticmethod
    def _state(instance):
        state = instance.__dict__.get(_STATE_KEY)
        if state is None:
            state = instance.__dict__.setdefault(_STATE_KEY, threading.local())
        return state

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        state = self._state(instance)
        if not hasattr(state, self.name):
            setattr(state, self.name, self.default_factory())
        return getattr(state, self.name)

    def __set__(self, instance, value):
        setattr(self._state(instance), self.name, value)


class ThreadLocalStateMixin:
    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop(_STATE_KEY, None)
        return state
