# -*- coding: future_fstrings -*-
import caracal
from caracal import log, pckgdir, notebooks
import sys
import os
from datetime import datetime
import stimela
import glob
import shutil
import traceback
import itertools
try:
   from urllib.parse import urlencode
except ImportError:
   from urllib import urlencode
 
import ruamel.yaml
assert ruamel.yaml.version_info >= (0, 12, 14)

from collections import OrderedDict
from caracal.dispatch_crew import utils

REPORTS = True

class worker_administrator(object):
    def __init__(self, config, workers_directory,
                 prefix=None, configFileName=None,
                 add_all_first=False, singularity_image_dir=None,
                 start_worker=None, end_worker=None,
                 container_tech='docker', generate_reports=True):

        self.config = config
        self.singularity_image_dir = singularity_image_dir
        self.container_tech = container_tech
        self.msdir = self.config['general']['msdir']
        self.input = self.config['general']['input']
        self.output = self.config['general']['output']
        self.obsinfo = self.config['general']['output'] + '/obsinfo'
        self.reports = self.config['general']['output'] + '/reports'
        self.diagnostic_plots = self.config['general']['output'] + \
            '/diagnostic_plots'
        self.configFolder = self.config['general']['output'] + '/cfgFiles'
        self.caltables = self.config['general']['output'] + '/caltables'
        self.masking = self.config['general']['output'] + '/masking'
        self.continuum = self.config['general']['output'] + '/continuum'
        self.crosscal_continuum = self.config['general']['output'] + '/continuum/crosscal'
        self.cubes = self.config['general']['output'] + '/cubes'
        self.mosaics = self.config['general']['output'] + '/mosaics'
        self.generate_reports = generate_reports
        self.timeNow = '{:%Y%m%d-%H%M%S}'.format(datetime.now())
        self.ms_extension = self.config["getdata"]["extension"]

        self._msinfo_cache = {}

        self.logs_symlink = self.config['general']['output'] + '/logs'
        self.logs = "{}-{}".format(self.logs_symlink, self.timeNow)

        if not self.config['general']['rawdatadir']:
            self.rawdatadir = self.config['general']['rawdatadir'] = self.msdir
        else:
            self.rawdatadir = self.config['general']['rawdatadir']

        self.virtconcat = False
        self.workers_directory = workers_directory
        # Add workers to packages
        if workers_directory:
            sys.path.append(self.workers_directory)
        self.workers = []
        last_mandatory = 2 # index of last mendatory worker
        # general, getdata and obsconf are all mendatory. 
        # That's why the lowest starting index is 2 (third element)
        start_idx = last_mandatory
        end_idx = len(self.config.keys())
        workers = []

        if start_worker and start_worker not in self.config.keys():
            raise RuntimeError("Requested --start-worker '{0:s}' is unknown. Please check your options".format(start_worker))
        if end_worker and end_worker not in self.config.keys():
            raise RuntimeError("Requested --end-worker '{0:s}' is unknown. Please check your options".format(end_worker))

        for i, (name, opts) in enumerate(self.config.items()):
            if name.find('general') >= 0 or name == "schema_version":
                continue
            if name.find('__') >= 0:
                worker = name.split('__')[0] + '_worker'
            else:
                worker = name + '_worker'
            if name == start_worker and name == end_worker:
                start_idx = len(workers)
                end_idx = len(workers)
            elif  name == start_worker:
                start_idx = len(workers)
            elif name == end_worker:
                end_idx = len(workers)
            workers.append((name, worker, i))
        
        if end_worker in list(self.config.keys())[:last_mandatory]:
            self.workers = workers[:last_mandatory]
        else:
            start_idx = max(start_idx, last_mandatory)
            end_idx = max(end_idx, last_mandatory)
            self.workers = workers[:last_mandatory] + workers[start_idx:end_idx+1]

        self.prefix = prefix or self.config['general']['prefix']

        # Get possible flagsets for reduction
        self.flags = {"legacy": ["legacy"]}
        for _name, _worker, i in self.workers:
            try:
                wkr = __import__(_worker)
            except ImportError:
                traceback.print_exc()
                raise ImportError('Worker "{0:s}" could not be found at {1:s}'.format(
                    _worker, self.workers_directory))

            if hasattr(wkr, "FLAG_NAMES"):
                self.flags[_name] = ["_".join(
                    [_name, suffix]) if suffix else _name for suffix in wkr.FLAG_NAMES]

        self.recipes = {}
        # Workers to skip
        self.skip = []
        # Initialize empty lists for ddids, leave this up to get data worker to define
        self.init_names([])
        if config["general"]["prep_workspace"]:
            self.init_pipeline()

        # save configuration file
        configFileName = os.path.splitext(configFileName)[0]
        outConfigName = '{0:s}_{1:s}.yml'.format(
            configFileName, self.timeNow)

        with open(self.configFolder+'/'+outConfigName, 'w') as outfile:
            ruamel.yaml.dump(self.config, outfile,
                             Dumper=ruamel.yaml.RoundTripDumper)

    def init_names(self, dataid):
        """ iniitalize names to be used throughout the pipeline and associated
            general fields that must be propagated
        """
        self.dataid = dataid

        # names of all MSs
        self.msnames = []
        # basenames of all MSs (sans extension)
        self.msbasenames = []
        # filename prefixes for outputs (formed up as prefix-msbase)
        self.prefix_msbases = []

        for item in 'rawdatadir input msdir output'.split():
            value = getattr(self, item, None)
            if value:
                setattr(self, item, value)

        if self.rawdatadir:
            for dataid in self.dataid:
                pattern = os.path.join(self.rawdatadir, f"{dataid}.{self.ms_extension}")
                msnames = [os.path.basename(ms) for ms in glob.glob(pattern)]
                msbases = [os.path.splitext(ms)[0] for ms in msnames]
                self.msnames += msnames
                self.msbasenames += msbases
                self.prefix_msbases += [ f"{self.prefix}-{x}" for x in msbases]
            self.nobs = len(self.msnames)

        for item in 'refant fcal bpcal gcal target xcal'.split():
            value = getattr(self, item, None)
            if value and len(value) == 1:
                value = value*self.nobs
                setattr(self, item, value)

    def get_msinfo(self, msname):
        """Returns info dict corresponding to an MS. Caches and reloads as needed"""
        msinfo_file = os.path.splitext(msname)[0] + "-summary.json"
        msinfo_path = os.path.join(self.msdir, msinfo_file)
        msdict, mtime_cache = self._msinfo_cache.get(msname, (None, 0))
        if not os.path.exists(msinfo_path):
            raise RuntimeError(f"MS summary file {msinfo_file} not found at expected location. This is a bug or "
                                "a misconfiguration. Was the MS transformed properly?")
        # reload cached dict if file on disk is newer
        mtime = os.path.getmtime(msinfo_path)
        if msdict is None or mtime > mtime_cache:
            with open(msinfo_path, 'r') as f:
                msdict = ruamel.yaml.load(f, ruamel.yaml.RoundTripLoader)
            self._msinfo_cache[msname] = msdict, mtime
        return msdict

    ### The following three methods provide MS naming services for workers

    def form_msname(self, msbase, label=None, field=None):
        """
        Given a base MS name, an optional label, and an optional field name, return the full MS name
        """
        label = '' if not label else '-' + label
        field = '' if not field else '-' + utils.filter_name(field)
        return f'{msbase}{field}{label}.{self.ms_extension}'

    def get_mslist(self, iobs, label="", target=False):
        """
        Given an MS number (0...nobs-1), and an optional label, returns list of corresponding MSs.
        If target is True, this will be one MS per each (split-out) target.
        If target is False, the list will contain just the single MS.
        Applies label in both cases.
        """
        msbase = self.msbasenames[iobs]
        if target:
            return [self.form_msname(msbase, label, targ) for targ in self.target[iobs]]
        else:
            return [self.form_msname(msbase, label)]

    def get_target_mss(self, label=None):
        """
        Given an MS label, returns a tuple of unique_targets, all_mss, mss_per_target
        Where all_mss is a list of all MSs to be processed for all targets, and mss_per_target maps target field
        to associated list of MSs
        """
        target_msfiles = OrderedDict()
        # self.target is a list of lists of targets, per each MS
        for msbase, targets in zip(self.msbasenames, self.target):
            for targ in targets:
                target_msfiles.setdefault(targ, []).append(self.form_msname(msbase, label, targ))
        # collect into flat list of MSs
        target_ms_ls = list(itertools.chain(*target_msfiles.values()))
        return list(target_msfiles.keys()), target_ms_ls, target_msfiles

    def parse_cabspec_dict(self, cabspec_seq):
        """Turns sequence of cabspecs into a Stimela cabspec dict"""
        cabspecs = OrderedDict()
        speclists = OrderedDict()
        # collect all specs encountered, sort them by cab
        for spec in cabspec_seq:
            name, version, tag = spec["name"], spec.get("version") or None, spec.get("tag") or None
            if not version and not tag:
                log.warning(f"Neither version nor tag specified for cabspec {name}, ignoring")
                continue
            speclists.setdefault(name, []).append((version, tag))
        # now process each cab's list of specs.
        for name, speclist in speclists.items():
            if len(speclist) == 1:
                version, tag = speclist[0]
                if version is None:
                    log.info(f"  {name}: forcing tag {tag} for all invocations")
                    cabspecs[name] = dict(tag=tag, force=True)
                    continue
                elif tag is None:
                    log.info(f"  {name}: forcing version {version} for all invocations")
                    cabspecs[name] = dict(version=version)
                    continue
            # else make dict of version: tag pairs
            cabspecs[name] = dict(version={version: tag for version, tag in speclist}, force=True)
            for version, tag in speclist:
                log.info(f"  {name}: using tag {tag} for version {version}")
        return cabspecs

    def init_pipeline(self):
        def make_symlink(link, target):
            if os.path.lexists(link):
                if os.path.islink(link):
                    os.unlink(link)  # old symlink can go
                else:
                    log.warning("{} already exists and is not a symlink, can't relink".format(link))
                    return False
            if not os.path.lexists(link):
                os.symlink(target, link)
                log.info("{} links to {}".format(link, target))

        # First create input folders if they don't exist
        if not os.path.exists(self.input):
            os.mkdir(self.input)
        if not os.path.exists(self.output):
            os.mkdir(self.output)
        if not os.path.exists(self.rawdatadir):
            os.mkdir(self.rawdatadir)
        if not os.path.exists(self.obsinfo):
            os.mkdir(self.obsinfo)
        if not os.path.exists(self.logs):
            os.mkdir(self.logs)
        log.info("output directory for logs is {}".format(self.logs))
        make_symlink(self.logs_symlink, os.path.basename(self.logs))
        if not os.path.exists(self.reports):
            os.mkdir(self.reports)
        if not os.path.exists(self.diagnostic_plots):
            os.mkdir(self.diagnostic_plots)
        if not os.path.exists(self.configFolder):
            os.mkdir(self.configFolder)
        if not os.path.exists(self.caltables):
            os.mkdir(self.caltables)
        if not os.path.exists(self.masking):
            os.mkdir(self.masking)
        if not os.path.exists(self.continuum):
            os.mkdir(self.continuum)
        if not os.path.exists(self.cubes):
            os.mkdir(self.cubes)
        # create proper logfile and start flushing
        # NB (Oleg): placing this into output rather than output/logs to make the reporting notebooks easier
        CARACAL_LOG_BASENAME = 'log-caracal.txt'
        caracal.CARACAL_LOG = os.path.join(self.logs, CARACAL_LOG_BASENAME)
        caracal.log_filehandler.setFilename(caracal.CARACAL_LOG, delay=False)

        # placing a symlink into logs to appease Josh
        make_symlink(os.path.join(self.output, CARACAL_LOG_BASENAME),
                     os.path.join(os.path.basename(self.logs), CARACAL_LOG_BASENAME))

        # Copy input data files into pipeline input folder
        log.info("Copying MeerKAT input files into input folder")
        datadir = "{0:s}/data/meerkat_files".format(pckgdir)
        for filename in os.listdir(datadir):
            src = os.path.join(datadir, filename)
            dest = os.path.join(self.input, filename)
            if not os.path.exists(dest):
                if os.path.isdir(src):
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest, follow_symlinks=False)

        # Copy standard notebooks
        self._init_notebooks = self.config['general']['init_notebooks']
        self._report_notebooks = self.config['general']['report_notebooks']
        all_nbs = set(self._init_notebooks) | set(self._report_notebooks)
        if all_nbs:
            notebooks.setup_default_notebooks(all_nbs, output_dir=self.output, prefix=self.prefix, config=self.config)

    def enable_task(self, config, task):
        return task in config and config[task].get("enable")

    def run_workers(self):
        """ Runs the  workers """
        report_updated = False

        for _name, _worker, i in self.workers:
            try:
                worker = __import__(_worker)
            except ImportError:
                traceback.print_exc()
                raise ImportError('Worker "{0:s}" could not be found at {1:s}'.format(
                    _worker, self.workers_directory))

        if self.config["general"]["cabs"]:
            log.info("Configuring cab specification overrides")
            cabspecs_general = self.parse_cabspec_dict(self.config["general"]["cabs"])
        else:
            cabspecs_general = {}

        active_workers = []
        # first, check that workers import, and check their configs
        for _name, _worker, i in self.workers:
            config = self.config[_name]
            if 'enable' in config and not config['enable']:
                self.skip.append(_worker)
                continue
            log.info("Configuring worker {}".format(_name))
            try:
                worker = __import__(_worker)
            except ImportError:
                log.error('Error importing worker "{0:s}" from {1:s}'.format(_worker, self.workers_directory))
                raise
            if hasattr(worker, 'check_config'):
                worker.check_config(config)
            # check for cab specs
            cabspecs = cabspecs_general
            if config["cabs"]:
                cabspecs = cabspecs.copy()
                cabspecs.update(self.parse_cabspec_dict(config["cabs"]))
            active_workers.append((_name, worker, config, cabspecs))

        # now run the actual pipeline
        #for _name, _worker, i in self.workers:
        for _name, worker, config, cabspecs in active_workers:
            # Define stimela recipe instance for worker
            # Also change logger name to avoid duplication of logging info
            label = getattr(worker, 'LABEL', None)
            if label is None:
                # if label is not set, take filename, and split off _worker.py
                label =  os.path.basename(worker.__file__).rsplit("_", 1)[0]

            recipe = stimela.Recipe(label,
                                    ms_dir=self.msdir,
                                    singularity_image_dir=self.singularity_image_dir,
                                    log_dir=self.logs,
                                    cabspecs=cabspecs,
                                    logfile=False, # no logfiles for recipes
                                    logfile_task=f'{self.logs}/log-{label}-{{task}}-{self.timeNow}.txt')

            recipe.JOB_TYPE = self.container_tech
            self.CURRENT_WORKER = _name
            # Don't allow pipeline-wide resume
            # functionality
            os.system('rm -f {}'.format(recipe.resume_file))
            # Get recipe steps
            # 1st get correct section of config file
            log.info("{0:s}: initializing".format(label), extra=dict(color="GREEN"))
            worker.worker(self, recipe, config)
            log.info("{0:s}: running".format(label))
            recipe.run()
            log.info("{0:s}: finished".format(label))

            # this should be in the cab cleanup code, no?

            casa_last = glob.glob(self.output + '/*.last')
            for file_ in casa_last:
                os.remove(file_)

            # update report at end of worker if so configured
            if self.generate_reports and config["report"]:
                self.regenerate_reports()
                report_updated = True
            else:
                report_updated = False

        # generate final report
        if self.config["general"]["final_report"] and self.generate_reports and not report_updated:
            self.regenerate_reports()

        log.info("pipeline run complete")

    def regenerate_reports(self):
        notebooks.generate_report_notebooks(self._report_notebooks, self.output, self.prefix, self.container_tech)
