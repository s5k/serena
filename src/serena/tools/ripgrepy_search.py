from typing import Dict, List, Optional, Union
import json
import os
from pydantic import BaseModel, Field
from ripgrepy import RipGrepOut 

class RipGrepySearchResult(BaseModel):
    """Model representing the result of a RipGrepy search, with file paths as keys and formatted matches as values."""
    results: Dict[str, List[str]] = Field(default_factory=dict, description="Dictionary with file paths as keys and formatted matches as values")


class RipGrepySearch:
    """
    A tool to search for patterns in files using ripgrep via the ripgrepy Python package.
    This tool provides powerful code search capabilities with support for regular expressions,
    context lines, and .gitignore-aware searching.
    """

    def search(
        self,
        pattern: str,
        path: str = ".",
        context_lines_before: int = 0,
        context_lines_after: int = 0,
        paths_include_glob: Optional[str] = None,
        paths_exclude_glob: Optional[str] = None,
        include_gitignore: bool = False,
    ) -> Dict[str, List[str]]:
        """
        Search for a pattern using ripgrep with customizable context and glob patterns.
        
        Args:
            pattern: Regular expression pattern to search for
            path: Directory or file path to search in (default: current directory)
            context_lines_before: Number of lines to show before each match
            context_lines_after: Number of lines to show after each match
            paths_include_glob: Glob pattern to include specific files/directories
            paths_exclude_glob: Glob pattern to exclude specific files/directories
            include_gitignore: If True, search in files/directories normally ignored by .gitignore
        
        Returns:
            A formatted dictionary with file paths as keys and match content as values
        """
        try:
            # Import here to avoid dependency issues if ripgrepy is not installed
            from ripgrepy import Ripgrepy
            
            # Initialize Ripgrepy with the pattern and path
            rg = Ripgrepy(pattern, path)
            
            # Set context lines before and after matches
            if context_lines_before > 0:
                rg.before_context(context_lines_before)
            
            if context_lines_after > 0:
                rg.after_context(context_lines_after)
            
            # Include or exclude specific files/directories using glob patterns
            if paths_include_glob:
                rg.glob(paths_include_glob)
            
            if paths_exclude_glob:
                # The ! prefix in glob patterns indicates exclusion
                rg.glob(f"!{paths_exclude_glob}")
            
            # Include files/directories normally ignored by .gitignore if requested
            if include_gitignore:
                rg.no_ignore()
            
            # Include line numbers in the output
            rg.line_number()
            
            # First enable JSON output as required by as_dict
            rg.json()
            
            # Get results
            result = rg.run()

            # Execute the search
            matches = self.as_dict(result)

            # Format the results into the requested structure
            return self._format_matches(matches)
                
        except ImportError:
            raise ImportError(
                "The ripgrepy package is required for this tool. "
                "You can install it with: pip install ripgrepy"
            )
        except Exception as e:
            raise Exception(f"Error searching with ripgrep: {str(e)}")


    def _format_matches(self, matches: List[Dict]) -> Dict[str, List[str]]:
        """
        Format ripgrep matches into a dictionary with file paths as keys
        and formatted match lines as values.
        
        Args:
            matches: List of match dictionaries from ripgrep as_dict()
            
        Returns:
            A dictionary with file paths as keys and formatted match lines as values
        """
        formatted_results = {}
        
        for match in matches:
            if match.get("type") in ("match", "context"):
                data = match.get("data", {})
                
                # Get the file path
                path_info = data.get("path", {})
                file_path = path_info.get("text", "") if isinstance(path_info, dict) else ""
                
                # Convert to absolute path if possible
                if file_path:
                    file_path = os.path.abspath(file_path)
                else:
                    continue  # Skip if no valid file path
                
                # Get line number and text
                line_number = data.get("line_number", 0)
                line_text = data.get("lines", {}).get("text", "")
                
                # Format the line with number
                formatted_line = f" > {line_number}: {line_text}"
                
                # Add to results, creating a new entry if needed
                if file_path not in formatted_results:
                    formatted_results[file_path] = []
                
                # Check if we already have content for this file
                if not formatted_results[file_path]:
                    formatted_results[file_path] = [formatted_line.rstrip()]
                else:
                    # Append to existing content
                    content = formatted_results[file_path][0]
                    formatted_results[file_path] = [f"{content}\n{formatted_line.rstrip()}"]
        
        return formatted_results

    def as_dict(self, output: RipGrepOut) -> list:
        """
        Returns an array of objects with the match. The objects include
        file path, line number and matched value. This is in addition to the
        --json that can be passed to ripgrep and is designed for simple ripgrep use

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
        if "--json" not in output.command:
            raise TypeError("To use as_dict, use the json() method")
        out = output._output.splitlines()
        holder = []
        for line in out:
            data = json.loads(line)
            if data["type"] in ("match", "context"):
                holder.append(data)
        return holder