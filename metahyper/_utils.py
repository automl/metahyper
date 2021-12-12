import atexit
import fcntl


class Locker:
    def __init__(self, lock_path):
        atexit.register(self.__del__)
        self.lock_file = lock_path.open("a")  # a for security

    def __del__(self):
        self.lock_file.close()

    def release_lock(self):
        fcntl.lockf(self.lock_file, fcntl.LOCK_UN)

    def acquire_lock(self):
        try:
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
