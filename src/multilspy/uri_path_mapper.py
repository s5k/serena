"""
Module providing URI to path mapping functionality with caching for the LSP integration.
Provides transformation of standard LSP responses to include relativePath information.
"""

import logging
import os
import pathlib
import json

from pathlib import Path, PurePath
from typing import Dict, Optional, Union, Any, List, Tuple, cast

from multilspy.lsp_protocol_handler.lsp_constants import LSPConstants
from multilspy.multilspy_utils import PathUtils


class UriPathMapper:
    """
    A class that handles mapping between URIs and file paths with efficient caching.
    
    This is used to transform standard LSP responses (which only contain URIs)
    to include relativePath information needed by Serena's internal data structures.
    """
    
    def __init__(self, repository_root_path: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the URI Path Mapper.
        
        :param repository_root_path: The absolute path to the repository root
        :param logger: Optional logger for debug information
        """
        self.repository_root_path = repository_root_path
        self.logger = logger or logging.getLogger(__name__)
        
        # Cache mappings for better performance
        self._uri_to_absolute_path: Dict[str, str] = {}
        self._uri_to_relative_path: Dict[str, str] = {}
        self._abs_to_relative_path: Dict[str, str] = {}
        
    def uri_to_absolute_path(self, uri: str) -> str:
        """
        Convert a URI to an absolute file path with caching.
        
        :param uri: The URI to convert
        :return: The absolute file path
        """
        if uri in self._uri_to_absolute_path:
            return self._uri_to_absolute_path[uri]
        
        abs_path = PathUtils.uri_to_path(uri)
        self._uri_to_absolute_path[uri] = abs_path
        return abs_path
    
    def absolute_to_relative_path(self, absolute_path: str) -> Optional[str]:
        """
        Convert an absolute path to a path relative to repository root with caching.
        
        :param absolute_path: The absolute path to convert
        :return: The relative path or None if not in repository
        """
        if absolute_path in self._abs_to_relative_path:
            return self._abs_to_relative_path[absolute_path]
        
        relative_path = PathUtils.get_relative_path(absolute_path, self.repository_root_path)
        if relative_path:
            self._abs_to_relative_path[absolute_path] = relative_path
        return relative_path
    
    def uri_to_relative_path(self, uri: str) -> Optional[str]:
        """
        Convert a URI directly to a relative path with caching.
        
        :param uri: The URI to convert
        :return: The relative path or None if not in repository
        """
        if uri in self._uri_to_relative_path:
            return self._uri_to_relative_path[uri]
        
        absolute_path = self.uri_to_absolute_path(uri)
        relative_path = self.absolute_to_relative_path(absolute_path)
        
        if relative_path:
            self._uri_to_relative_path[uri] = relative_path
        
        return relative_path
    
    def clear_cache(self) -> None:
        """Clear all cached path mappings."""
        self._uri_to_absolute_path.clear()
        self._uri_to_relative_path.clear()
        self._abs_to_relative_path.clear()
    
    def enrich_location(self, location: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich a Location object from LSP with absolutePath and relativePath fields.
        
        :param location: The Location object from LSP response
        :return: The enriched Location object
        """

        dataDumped = json.dumps(location)
        self.logger.log(f"URLPATH - enrich_location: {dataDumped} has returned.", logging.INFO)

        if not location or "uri" not in location:
            return location
        
        if "absolutePath" not in location:
            location["absolutePath"] = self.uri_to_absolute_path(location["uri"])
            
        if "relativePath" not in location:
            relative_path = self.absolute_to_relative_path(location["absolutePath"])
            if relative_path:
                location["relativePath"] = relative_path
                
        return location
    
    def enrich_symbol(self, symbol: Dict[str, Any], default_relative_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Enrich a Symbol object from LSP with location information including paths.
        
        :param symbol: The Symbol object from LSP response
        :param default_relative_path: Default relative path to use if location resolution fails
        :return: The enriched Symbol object
        """
        if not symbol:
            return symbol
        
        dataDumped = json.dumps(symbol)
        self.logger.log(f"URLPATH - enrich_symbol: {dataDumped} has returned.", logging.INFO)
            
        # For document symbols that may not have a location but have a range
        if "location" not in symbol and "range" in symbol and default_relative_path:
            absolute_path = os.path.join(self.repository_root_path, default_relative_path)
            uri = pathlib.Path(absolute_path).as_uri()
            
            symbol["location"] = {
                "uri": uri,
                "range": symbol["range"],
                "absolutePath": absolute_path,
                "relativePath": default_relative_path
            }
        elif "location" in symbol:
            # Add line and column as direct properties for easier access
            symbol["location"] = self.enrich_location(symbol["location"])

        if "selectionRange" not in symbol:
                if "range" in symbol:
                    symbol["selectionRange"] = symbol["range"]
                else:
                    symbol["selectionRange"] = symbol["location"]["range"]

        # Process children recursively
        if "children" in symbol and symbol["children"]:
            symbol["children"] = [
                self.enrich_symbol(child, default_relative_path) for child in symbol["children"]
            ]
            
        return symbol
    
    def transform_response(self, 
                         response: Union[Dict[str, Any], List[Dict[str, Any]], None], 
                         default_relative_path: Optional[str] = None) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """
        Transform a standard LSP response to include path information.
        
        This method handles different response types from LSP methods:
        - Single Location objects
        - Lists of Location objects
        - Symbol objects (DocumentSymbol, SymbolInformation)
        - Lists of Symbol objects
        
        :param response: The LSP response to transform
        :param default_relative_path: Default relative path to use for symbol enrichment
        :return: The transformed response
        """
        if response is None:
            return None
            
        if isinstance(response, list):
            # For lists of locations or symbols
            return [self.transform_response(item, default_relative_path) for item in response]
            
        if isinstance(response, dict):
            # Is it a Location?
            if "uri" in response and "range" in response:
                return self.enrich_location(response)
                
            # Is it a Symbol?
            if "name" in response and "kind" in response:
                return self.enrich_symbol(response, default_relative_path)
                
            # Some other dict response
            return response
            
        # Other types like scalar values
        return response
