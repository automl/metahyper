import atexit
import fcntl


class Locker:
    def __init__(self, lock_path, logger):
        self.logger = logger
        atexit.register(self.__del__)
        self.lock_path = lock_path
        self.lock_file = self.lock_path.open("a")  # a for security

    def __del__(self):
        self.lock_file.close()

    def release_lock(self):
        self.logger.debug(f"Release lock for {self.lock_path}")
        fcntl.lockf(self.lock_file, fcntl.LOCK_UN)

    def acquire_lock(self):
        try:
            self.logger.debug(f"Acquired lock for {self.lock_path}")
            fcntl.lockf(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self.logger.debug(f"Failed to acquire lock for {self.lock_path}")
            return False
