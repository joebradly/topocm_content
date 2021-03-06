#!/usr/bin/env python
"""Process notebook by running cells and caching outputs on the filesystem.

Usage: python cachedoutput.py nb.ipynb > out.ipynb
"""

# Copyright (c) Min RK
# Distributed under the terms of the Modified BSD License

import errno
from hashlib import sha1
import io
import json
import os
import sys
import codecs

from traitlets import Unicode, Integer

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor


class OutputCache(dict):
    """A dict-like API for writing data to JSON files in a directory
    
    Each item in the cache is a file in the directory.
    Directories are nested one-level deep to avoid too many files in the top-level directory.
    Each file is in a location of the form:
    
    root/ab/cdef1234.json
    """
    def __init__(self, path):
        self.root = path
    
    def _path(self, key):
        return os.path.join(self.root, key[:2], key[2:] + '.json')
    
    def __contains__(self, key):
        return os.path.exists(self._path(key))
    
    def __getitem__(self, key):
        if key not in self:
            raise KeyError(key)
        with codecs.open(self._path(key), encoding='utf8') as f:
            try:
                return json.load(f)
            except ValueError:
                os.remove(self._path(key))
                raise
    
    def __setitem__(self, key, value):
        path = self._path(key)
        try:
            os.makedirs(os.path.dirname(path))
        except OSError as e:
            if e.errno == errno.EEXIST:
                pass
                # dir already exists
            else:
                raise
        with codecs.open(self._path(key), 'w', encoding='utf8') as f:
            json.dump(value, f)

from collections import namedtuple
setup_cell = namedtuple('setup_cell', ('index', 'cell'))

class CachedOutputPreprocessor(ExecutePreprocessor):
    """NbConvert preprocessor that caches output
    
    Output is stored in a cache directory, and only recomputed if needed.
    """
    
    cache_directory = Unicode('/tmp/nb_output_cache', config=True)
    
    setup_cells = Integer(1, config=True,
        help="""The number of code cells at the beginning of the notebook to use as setup for other cells.
        
        If these cells change, all cached outputs are invalidated.
        When running a cell, these cells will be run first, but not other cells.
        """
    )
    
    def cache_key(self, source, cell_index):
        """Compute cache key for a cell
        
        Includes hash of setup code (if cell is not part of the setup itself)
        """
        sources = []
        for idx, cell in self.setup:
            if idx >= cell_index:
                break
            sources.append(cell.source)
        sources.append(source)
        return sha1('\n'.join(sources).encode('utf8')).hexdigest()
    
    def preprocess(self, nb, resources):
        self.cache = OutputCache(self.cache_directory)
        self.kernel_name = nb.metadata.get('kernelspec', {}).get('name', 'python')
        self.kernel_path = resources.get('metadata', {}).get('path')
        # extract setup cells
        self.setup = []
        for idx, cell in enumerate(nb['cells']):
            if len(self.setup) >= self.setup_cells:
                break
            if cell.cell_type == 'code':
                self.setup.append(setup_cell(idx, cell))
        
        self.end_setup_index = idx
        self.setup_to_run = list(self.setup)
        # skip one level of super
        return super(CachedOutputPreprocessor, self).preprocess(nb, resources)
    
    def preprocess_cell(self, cell, resources, cell_index):
        if cell.cell_type != 'code':
            return cell, resources
        
        key = self.cache_key(cell.source, cell_index)
        if key in self.cache:
            self.log.debug("Cache hit[%i]: %s", cell_index, key)
            cell.outputs = [ nbformat.NotebookNode(output) for output in self.cache[key] ]
        else:
            cell.outputs = self.run_cell(cell, cell_index)
            self.cache[key] = cell.outputs
        return cell, resources
    
    def run_cell(self, cell, cell_index):
        """Run a single cell, including setup if needed"""
        run_cell = super(CachedOutputPreprocessor, self).run_cell
        while self.setup_to_run:
            idx, setup_cell = self.setup_to_run.pop(0)
            if idx < cell_index:
                run_cell(setup_cell)
            if idx == cell_index:
                # The current cell is part of setup
                return run_cell(cell)
        return run_cell(cell)

def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    p = CachedOutputPreprocessor(enabled=True)
    
    for path in sys.argv[1:]:
        nb = nbformat.read(path, as_version=4)
        p(nb, {'metadata': {'path': os.path.dirname(path) or '.'}})
        nbformat.write(nb, sys.stdout)

if __name__ == '__main__':
    main()
