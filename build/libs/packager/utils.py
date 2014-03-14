#!/usr/bin/env python
''' Utilities for Packager scripts '''

import os
import re
import sys
import copy
import glob
import fcntl
import errno
import Queue
import select
import shutil
import tarfile
import logging
import fnmatch
import operator
import tempfile
import itertools
import subprocess

from ConfigParser import SafeConfigParser

log = logging.getLogger("pkg.%s" %__name__)

class Utils(object):
    ''' Utilities for packager '''

    @staticmethod
    def expanduser(dirnames=None):
        '''Expand user ~ in directory names'''
        if dirnames is None:
            return None
        dirnames = [dirnames] if type(dirnames) is str else dirnames
        absdirs = [os.path.abspath(os.path.expanduser(dirname)) \
                  for dirname in dirnames]
        return absdirs[0] if len(absdirs) == 1 else absdirs

    @staticmethod
    def create_dir(dirname):
        dirname = os.path.expanduser(dirname)
        if not os.path.exists(dirname):
            log.debug('Creating directory: %s' %dirname)
            os.makedirs(dirname)
        else:
            log.debug('Dir %s exists' %dirname)
        return dirname

    @staticmethod
    def copyfiles(files, dirs):
        files = [files] if type(files) is str else files
        dirs  = [dirs] if type(dirs) is str else dirs
        copyiter = itertools.product(files, dirs)
        for item in copyiter:
            log.debug('Copying ({0}) to dir ({1})'.format(*item))
            shutil.copy(*item)
            
    def read_async(self, fd):
        '''read data from a file descriptor, ignoring EAGAIN errors'''
        try:
            return fd.read()
        except IOError, e:
            if e.errno != errno.EAGAIN:
                raise e
            else:
                return ''

    def log_fds(self, fds):
        ''' print logs to stdout, stream and file handlers '''
        for fd in fds:
            out = self.read_async(fd)
            if out:
                sys.stdout.write(out)
                log.parent.handlers[1].stream.write(out)
                log.parent.handlers[1].stream.flush()

    def make_async(self, fd):
        '''add O_NONBLOCK flag to a file descriptor'''
        fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) | os.O_NONBLOCK)

    def exec_cmd_out(self, cmd, wd=''):
        ''' execute given command after chdir to given directory 
            and return output
        '''
        wd = wd or os.getcwd()
        wd = os.path.normpath(wd)
        log.debug('cd %s; cmd: %s' %(wd, cmd))
        proc = subprocess.Popen(cmd, shell=True, cwd=wd, 
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)  
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            log.error(stdout)
            log.error(stderr)
            raise RuntimeError('Cmd: %s; **FAILED**' %cmd)
        return stdout.strip('\n'), stderr.strip('\n')
  
    def exec_cmd(self, cmd, wd=''):
        ''' DEBUG execute given command after chdir to given directory '''
        wd = wd or os.getcwd()
        wd = os.path.normpath(wd)
        log.debug('cd %s; cmd: %s' %(wd, cmd))
        proc = subprocess.Popen(cmd, shell=True, cwd=wd, 
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        self.make_async(proc.stdout)
        self.make_async(proc.stderr)
        while True:
            rlist, wlist, xlist = select.select([proc.stdout, proc.stderr], [], [])
            self.log_fds(rlist)
            if proc.poll() is not None:
                self.log_fds([proc.stdout, proc.stderr])
                break
        proc.communicate()
        if proc.returncode != 0:
            log.error('Cmd: %s; **FAILED**' %cmd)
            raise RuntimeError('Cmd: %s; **FAILED**' %cmd)
        
    @staticmethod
    def str_to_list(tstr, force=False):
        ''' convert string separated with comma into a list '''
        tstr = tstr.replace('\n', '')
        sstr = [sstr.strip() for sstr in tstr.split(',')]
        if force:
            return sstr
        else:
            return tstr if tstr.rfind(',') < 0 else sstr

    @staticmethod
    def rtrv_lsv_file_info(fname):
        ''' read given file and create a line with every line as list element '''
        file_info_list = []
        if fname and os.path.isfile(fname) and\
           os.lstat(fname).st_size != 0:
            with open(fname, 'r') as fid:
                file_info_list = fid.read().split('\n')
        return filter(None, file_info_list)

    def get_md5(self, files):
        md5dict = {}
        if files is None or len(files) == 0:
            return
        files = [files] if type(files) is str else files
        for filename in files:
            if not os.path.isfile(filename):
                raise RuntimeError('File (%s) is not present' %filename)
            md5out = self.exec_cmd_out('md5sum %s' %filename)
            md5dict[filename] = md5out[0].split()[0]
        return md5dict

    def check_package_md5(self, pkginfo):
        for pkg in pkginfo.keys():
            if pkginfo[pkg]['location'] == '':
                raise RuntimeError('Location of the Package (%s) is empty' %pkg)
            pkgfile = self.get_file_list(pkginfo[pkg]['location'], pkginfo[pkg]['file'], True)
            if len(pkgfile) == 0:
                raise RuntimeError('Package file for package (%s) is not present in (%s)' %(
                                    pkg, pkginfo[pkg]['location']))
            pkgfile = self.get_latest_file(pkgfile)
            actual_md5 = self.get_md5(pkgfile)
            if actual_md5 is None:
                raise RuntimeError('MD5 checksum is empty for file (%s)' %pkgfile)
            if pkginfo[pkg]['md5'] != actual_md5[pkgfile]:
                raise RuntimeError('MD5 Checksum validation for Package (%s) failed' %(
                                    pkgfile))
            else:
                pkginfo[pkg]['found_at'] = pkgfile
            
    @staticmethod
    def get_file_list(dirs, pattern, recursion=True):
        ''' get a list of files that matches given pattern '''
        filelist = []
        dirs = [dirs] if type(dirs) is str else dirs
        for dirname in dirs:
            if recursion:
                for dir, sdir, flist in os.walk(dirname):
                    filelist += [os.path.abspath('%s/%s' %(dir, fname)) \
                                    for fname in fnmatch.filter(flist, pattern)]  
            else:
                filelist += [os.path.abspath('%s/%s' %(dirname, fname)) \
                              for fname in fnmatch.filter(os.listdir(dirname), pattern)]
        return filter(None, filelist)

    @staticmethod
    def get_files_by_pattern(patterns):
        ''' get a list of files that matches given pattern '''
        filelist = []
        patterns = [patterns] if type(patterns) is str else patterns
        for pattern in patterns:
            filelist.extend(glob.glob(pattern))
        return filter(None, filelist)

    @staticmethod
    def get_latest_file(filelist):
        ''' get the latest file based on creation time from the 
            given list of files
        '''
        if len(filelist) == 0:
            return ''
        filelist = [filelist] if type(filelist) is str else filelist
        ctime = operator.attrgetter('st_ctime')
        filestats = map(lambda fname: (ctime(os.lstat(fname)), fname), filelist)
        if len(filestats) == 0:
            raise RuntimeError('File list is empty; file list (%s) for given filelist (%s)' %(
                                filestats, filelist))
        return sorted(filestats)[-1][-1]

    @staticmethod
    def create_tgz(filename, srcdir, untar_as=''):
        ''' create a compressed tar file from the given directory
        '''
        log.info('Create tgz file (%s) from Dir (%s) and to untar as (\'%s\')' %(
                 filename, srcdir, untar_as))
        tar = tarfile.open(filename, 'w:gz')
        tar.add(srcdir, arcname=untar_as)
        tar.close()
        return os.path.abspath(filename)

    def create_pkgs_tgz(self, dirnames=None):
        '''Create tgz from each repo directory'''
        dirnames = dirnames or self.repo_dirs
        for repo_dir in dirnames:
            tgz_name = '%s_%s-%s~%s.tgz' %(repo_dir, self.branch, 
                              self.id, self.sku)
            log.info('Create (%s) file' %tgz_name)
            self.create_tgz(tgz_name, repo_dir)

    def parse_git_cfg_file(self, cfgfile):
        ''' parse git config file and return a dict of git config '''
        tmpfile = tempfile.NamedTemporaryFile()
        with open(cfgfile, 'r') as fileid:
            tmpfile.write('%s\n' %re.sub(r'\n\t', '\n', fileid.read()))
            tmpfile.flush()
        return self.parse_cfg_file(tmpfile.name)
        
    def parse_cfg_file(self, cfg_files, additems=None):
        ''' parse the given config files and return a dictionary
            with sections as keys and its items as dictionary items
        '''
        parsed_dict = {}
        parser = SafeConfigParser()
        cfg_files = [cfg_files] if type(cfg_files) is str else cfg_files
        for cfg_file in cfg_files:
            temp_parser = SafeConfigParser()
            temp_parser.read('cfg_file')
            common_sections = list(set(temp_parser.sections()) & \
                                   set(parser.sections()))
            if len(common_sections) != 0:
                raise RuntimeError('Duplication Section Error while parsing '
                           '(%s): %s' %(cfg_file, "\n".join(common_sections)))
            parsed_files = parser.read(cfg_file)
            if cfg_file not in parsed_files:
                raise RuntimeError('Unable to parse (%s), '
                                   'No such file or invalid format' %cfg_file)
            del temp_parser

        for sect in parser.sections():
            parsed_dict[sect] = dict((iname, self.str_to_list(ival)) \
                                   for iname, ival in parser.items(sect))
            if additems != None:
                for item in additems.keys():
                    parsed_dict[sect][item] = copy.deepcopy(additems[item])
        return parsed_dict

    def import_file(self, cfile):
        ''' import a module based on its absolute path '''
        cfile = os.path.abspath(os.path.expanduser(cfile))
        cfile_name = os.path.basename(cfile).strip('.py')
        cfile_dir  = os.path.dirname(cfile)
        sys.path.append(cfile_dir)
        try:
            cfgfile = __import__(cfile_name)
        except ImportError, err:
            raise ImportError('Unable to import "%s" file.\nERROR: %s' %(cfile_name, err))
        return cfgfile

    def get_repodirs(self, *pkgcfgs):
        '''Get list of repo dirs defined for each package'''
        repo_dirs = []
        for pkgcfg in pkgcfgs:
            repo_dir = [pkgcfg[pkg]['repo'] for pkg in pkgcfg.keys()]
            repo_dirs.extend(repo_dir)
        return list(set(repo_dirs))

    def copy_pkg_files(self, pkginfo, error=True):
        ''' copy the packages present in location specified in package data structure
            to given destination directories
        '''
        for pkg in pkginfo.keys():
            if pkginfo[pkg]['found_at'] == '':
                if error:
                    raise IOError('Info about package file for '
                                  'Package (%s) is not found' %pkg)
                else:
                    log.warn('Info about package file for '
                             'Package (%s) is not found' %pkg)
            pkgfile = pkginfo[pkg]['found_at']
            destdirs = pkginfo[pkg]['repo']
            self.copyfiles(pkgfile, destdirs)

    def copy_built_pkg_files(self, targets=None, extra_dirs=None,
                             skips=None, error=True):
        ''' copy the contrail packages present in location specified in 
            package data structure to given destination directories
        '''
        targets = list(targets)
        targets = list(set(targets) - set(skips)) if skips else targets     
        for each in targets:
            pkgs = self.contrail_pkgs[each]['pkgs']
            pkgs = [pkgs] if type(pkgs) is str else pkgs
            for pkg in filter(None, pkgs):      
                if self.contrail_pkgs[each]['found_at'][pkg] == '':
                    if error:
                        raise IOError('Info about package file for '
                                      'Package (%s) is not found' %pkg)
                    else:
                        log.warn('Info about package file for '
                                 'Package (%s) is not found' %pkg)
                pkgfile = self.contrail_pkgs[each]['found_at'][pkg]
                destdirs = self.contrail_pkgs[each]['repo']
                if extra_dirs is not None:
                    destdirs.extend(list(extra_dirs))
                self.copyfiles(pkgfile, destdirs)

    def copy_to_artifacts(self):
        '''Copies rpm or deb files to artifacts directory'''
        if self.platform == 'ubuntu':
            builtdirs = [os.path.join(self.git_local_repo, 'build', 'debian'),\
                         os.path.join(self.git_local_repo, 'build', 'openstack')]
            toolsdirs = [os.path.join(self.git_local_repo, 'build', 'tools')]
            pattern = '*.deb'
        else:
            basedir = os.path.join(self.git_local_repo, 'controller', 'build',
                                   'package-build')
            builtdirs = [os.path.join(basedir, 'RPMS', 'noarch'),\
                         os.path.join(basedir, 'RPMS', 'x86_64')]
            toolsdirs = [os.path.join(basedir, 'TOOLS')]
            pattern = '*.rpm'
        for dirname in builtdirs:
            if not os.path.isdir(dirname):
                log.warn('Dir (%s) do not exists. Skipping...' %dirname)
                continue
            pkgfiles = self.get_file_list(dirname, pattern, False)
            for pkgfile in pkgfiles:
                log.debug('Copying (%s) to (%s)' %(pkgfile, self.artifacts_dir))
                shutil.copy(pkgfile, self.artifacts_dir)

        # copy iso to artifacts
        if os.path.isfile(self.imgname):
            log.debug('Copying ISO file (%s) to (%s)' %(
                           self.imgname, self.artifacts_dir))
            shutil.copy(self.imgname, self.artifacts_dir)
        else:
            log.warn('ISO file is not created yet. Skipping...')

        # copy tools tgz to artifacts-extras
        for dirname in toolsdirs:
            if not os.path.isdir(dirname):
                log.warn('Dir (%s) do not exists. Skipping...' %dirname)
                continue
            pkgfiles = self.get_file_list(dirname, '*.tgz', False)
            for pkgfile in pkgfiles:
                log.debug('Copying (%s) to (%s)' %(pkgfile, 
                           self.artifacts_extra_dir))
                shutil.copy(pkgfile, self.artifacts_extra_dir)
