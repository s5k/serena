import logging
import subprocess
from ripgrepy import Ripgrepy, RipGrepOut 
from json import loads

log = logging.getLogger(__name__)

class EnhancedRipGrepOut(RipGrepOut):

    @property
    def as_dict(self) -> list:
        """
        Returns an array of objects with the match. The objects include
        file path, line number and matched value. This is in addition to the
        --json that can be passed to ripgrep and is designed for simple ripgrep use
        + Override to add matching "context" in data

        :return: Array of matched objects
        :rtype: list

        The following is an example of the dict output.

        >>> [{'data': {'absolute_offset': 12,
        >>>   'line_number': 3,
        >>>   'lines': {'text': 'teststring\\n'},
        >>>   'path': {'text': '/tmp/test/test.lol'},
        >>>   'submatches': [{'end': 4, 'match': {'text': 'test'}, 'start': 0}]},
        >>> 'type': 'match' | 'context'}]
        """
        if "--json" not in self.command:
            raise TypeError("To use as_dict, use the json() method")
        out = self._output.splitlines()
        holder = []
        for line in out:
            try:
                data = loads(line)
                if "type" in data and data["type"] in ("match", "context"):
                    holder.append(data)
            except Exception as e:
                # TODO: Skip loads can't handle minified file, investigate later
                log.info(f"Error message: {str(e)}, json.loads cannot handle this line: {line}")
                continue
        return holder

class EnhancedRipgrepy(Ripgrepy):
    """
    An enhanced version of Ripgrepy that includes a method
    to directly get the parsed JSON output as a list of dictionaries.
    """

    def run(self) -> EnhancedRipGrepOut:
        """
        Returns an instace of the EnhancedRipGrepOut object
        + Override to add cwd to subprocess

        :return: self
        :rtype: RipGrepOut
        """
        self.command.append(self.regex_pattern)
        self.command.append(self.path)
        output = subprocess.run(self.command, capture_output=True, cwd=self.path)
        if output.returncode == 0:
            self._output = output.stdout.decode("UTF-8")
        else:
            self._output = output.stderr.decode("UTF-8")

        return EnhancedRipGrepOut(self._output, self.command)