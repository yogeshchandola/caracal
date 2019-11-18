import sys
import os
import glob
import warnings
import stimela.dismissable as sdm
import stimela.recipe as stimela
import astropy
import shutil
from astropy.io import fits
import meerkathi
# Modules useful to calculate common barycentric frequency grid
from astropy.time import Time
from astropy.coordinates import SkyCoord
from astropy.coordinates import EarthLocation
from astropy import constants
import astropy.units as asunits
import re
import datetime
import numpy as np
import yaml
from meerkathi.dispatch_crew import utils
import itertools

# To split out cubes/<dir> from output/cubes/dir
def get_dir_path(string, pipeline): return string.split(pipeline.output)[1][1:]

def target_to_msfiles(targets, msnames, label, doppler=False):
    target_ls, target_msfiles, target_ms_ls, all_target = [], [], [], []

    for t in targets:  # list all targets per input ms and make a unique list of all target fields
        tmp = t.split(',')
        target_ls.append(tmp)
        for tt in tmp:
            all_target.append(tt)
    all_target = list(set(all_target))

    # make a list of all input ms file names for each target field
    for i, ms in enumerate(msnames):

        for t in target_ls[i]:
            tmp = utils.filter_name(t)
            if doppler:
                target_ms_ls.append(
                    '{0:s}-{1:s}{2:s}_mst.ms'.format(ms[:-3], tmp, label))
            else:
                target_ms_ls.append(
                    '{0:s}-{1:s}{2:s}.ms'.format(ms[:-3], tmp, label))

    for t in all_target:  # group ms files by target field name
        tmp = []
        for m in target_ms_ls:
            if m.find(utils.filter_name(t)) > -1:
                tmp.append(m)
        target_msfiles.append(tmp)

    return all_target, target_ms_ls, dict(list(zip(all_target, target_msfiles)))


def freq_to_vel(filename, reverse):
    C = 2.99792458e+8       # m/s
    HI = 1.4204057517667e+9  # Hz
    filename = filename.split(':')
    filename = '{0:s}/{1:s}'.format(filename[1], filename[0])
    if not os.path.exists(filename):
        meerkathi.log.info(
            'Skipping conversion for {0:s}. File does not exist.'.format(filename))
    else:
        with fits.open(filename, mode='update') as cube:
            headcube = cube[0].header
            if 'restfreq' in headcube:
                restfreq = float(headcube['restfreq'])
            else:
                restfreq = HI
                # add rest frequency to FITS header
                headcube['restfreq'] = restfreq

            # convert from frequency to radio velocity
            if 'FREQ' in headcube['ctype3'] and not reverse:
                headcube['cdelt3'] = -C * float(headcube['cdelt3']) / restfreq
                headcube['crval3'] = C * \
                    (1 - float(headcube['crval3']) / restfreq)
                # FITS standard for radio velocity as per
                # https://fits.gsfc.nasa.gov/standard40/fits_standard40aa-le.pdf

                headcube['ctype3'] = 'VRAD'
                if 'cunit3' in headcube:
                    # delete cunit3 because we adopt the default units = m/s
                    del headcube['cunit3']

            # convert from radio velocity to frequency
            elif 'VRAD' in headcube['ctype3'] and reverse:
                headcube['cdelt3'] = -restfreq * float(headcube['cdelt3']) / C
                headcube['crval3'] = restfreq * \
                    (1 - float(headcube['crval3']) / C)
                headcube['ctype3'] = 'FREQ'
                if 'cunit3' in headcube:
                    # delete cunit3 because we adopt the default units = Hz
                    del headcube['cunit3']
            else:
                if not reverse:
                    meerkathi.log.info(
                        'Skipping conversion for {0:s}. Input cube not in frequency.'.format(filename))
                else:
                    meerkathi.log.info(
                        'Skipping conversion for {0:s}. Input cube not in velocity.'.format(filename))


def remove_stokes_axis(filename):
    filename = filename.split(':')
    filename = '{0:s}/{1:s}'.format(filename[1], filename[0])
    if not os.path.exists(filename):
        meerkathi.log.info(
            'Skipping Stokes axis removal for {0:s}. File does not exist.'.format(filename))
    else:
        with fits.open(filename, mode='update') as cube:
            headcube = cube[0].header
            if headcube['naxis'] == 4 and headcube['ctype4'] == 'STOKES':
                cube[0].data = cube[0].data[0]
                del headcube['cdelt4']
                del headcube['crpix4']
                del headcube['crval4']
                del headcube['ctype4']
                if 'cunit4' in headcube:
                    del headcube['cunit4']
            else:
                meerkathi.log.info(
                    'Skipping Stokes axis removal for {0:s}. Input cube has less than 4 axis or the 4th axis type is not "STOKES".'.format(filename))


def fix_specsys(filename, specframe):
    # Reference frame codes below from from http://www.eso.org/~jagonzal/telcal/Juan-Ramon/SDMTables.pdf, Sec. 2.50 and
    # FITS header notation from
    # https://fits.gsfc.nasa.gov/standard40/fits_standard40aa-le.pdf
    specsys3 = {
        0: 'LSRD',
        1: 'LSRK',
        2: 'GALACTOC',
        3: 'BARYCENT',
        4: 'GEOCENTR',
        5: 'TOPOCENT'}[
        np.unique(
            np.array(specframe))[0]]
    filename = filename.split(':')
    filename = '{0:s}/{1:s}'.format(filename[1], filename[0])
    if not os.path.exists(filename):
        meerkathi.log.info(
            'Skipping SPECSYS fix for {0:s}. File does not exist.'.format(filename))
    else:
        with fits.open(filename, mode='update') as cube:
            headcube = cube[0].header
            if 'specsys' in headcube:
                del headcube['specsys']
            headcube['specsys3'] = specsys3

def make_pb_cube(filename, apply_corr):

    filename = filename.split(':')
    filename = '{0:s}/{1:s}'.format(filename[1], filename[0])
    if not os.path.exists(filename):
        meerkathi.log.info(
            'Skipping primary beam cube for {0:s}. File does not exist.'.format(filename))
    else:
        with fits.open(filename) as cube:
            headcube = cube[0].header
            datacube = np.indices(
                (headcube['naxis2'], headcube['naxis1']), dtype=np.float32)
            datacube[0] -= (headcube['crpix2'] - 1)
            datacube[1] -= (headcube['crpix1'] - 1)
            datacube = np.sqrt((datacube**2).sum(axis=0))
            datacube.resize((1, datacube.shape[0], datacube.shape[1]))
            datacube = np.repeat(datacube,
                                 headcube['naxis3'],
                                 axis=0) * np.abs(headcube['cdelt1'])
            sigma_pb = 17.52 / (headcube['crval3'] + headcube['cdelt3'] * (
                np.arange(headcube['naxis3']) - headcube['crpix3'] + 1)) * 1e+9 / 13.5 / 2.355
            # sigma_pb=headcube['crval3']+headcube['cdelt3']*(np.arange(headcube['naxis3'])-headcube['crpix3']+1)
            sigma_pb.resize((sigma_pb.shape[0], 1, 1))
            datacube = np.exp(-datacube**2 / 2 / sigma_pb**2)
            fits.writeto(filename.replace('image.fits','pb.fits'),
                datacube, header=headcube, overwrite=True)
            if apply_corr:
                fits.writeto(filename.replace('image.fits','pb_corr.fits'),
                    cube[0].data / datacube, header=headcube, overwrite=True)  # Applying the primary beam correction
            meerkathi.log.info('Created primary beam cube FITS {0:s}'.format(
                    filename.replace('image.fits', 'pb.fits')))


def calc_rms(filename, linemaskname):
    if linemaskname is None:
        if not os.path.exists(filename):
            meerkathi.log.info(
                'Noise not determined in cube for {0:s}. File does not exist.'.format(filename))
        else:
            with fits.open(filename) as cube:
                datacube = cube[0].data
                y = datacube[~np.isnan(datacube)]
            return np.sqrt(np.sum(y * y, dtype=np.float64) / y.size)
    else:
        with fits.open(filename) as cube:
            datacube = cube[0].data
        with fits.open(linemaskname) as mask:
            datamask = mask[0].data
            # select channels
            selchans = datamask.sum(axis=(2, 3)) > 0
            newcube = datacube[selchans]
            newmask = datamask[selchans]
            y2 = newcube[newmask == 0]
        return np.sqrt(np.nansum(y2 * y2, dtype=np.float64) / y2.size)


NAME = 'Make Line Cube'


def worker(pipeline, recipe, config):
    label = config['label']
    line_name = config['line_name']
    if label != '':
        flabel = '_' + label
    else:
        flabel = label
    all_targets, all_msfiles, ms_dict = target_to_msfiles(
        pipeline.target, pipeline.msnames, flabel, False)
    RA, Dec = [], []
    firstchanfreq_all, chanw_all, lastchanfreq_all = [], [], []
    mslist = ['{0:s}_{1:s}.ms'.format(did, config['label'])
              for did in pipeline.dataid]
    pipeline.prefixes = [
        '{2:s}-{0:s}-{1:s}'.format(did, config['label'],
            pipeline.prefix) for did in pipeline.dataid]
    prefixes = pipeline.prefixes
    restfreq = config.get('restfreq')

    for i, msfile in enumerate(all_msfiles):
        # Upate pipeline attributes (useful if, e.g., channel averaging was
        # performed by the split_data worker)
        msinfo = '{0:s}/{1:s}-obsinfo.json'.format(
            pipeline.output, msfile[:-3])
        meerkathi.log.info('Updating info from {0:s}'.format(msinfo))
        with open(msinfo, 'r') as stdr:
            spw = yaml.load(stdr)['SPW']['NUM_CHAN']
        meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(
            len(spw), ','.join(map(str, spw))))

        # Get first chan, last chan, chan width
        with open(msinfo, 'r') as stdr:
            chfr = yaml.load(stdr)['SPW']['CHAN_FREQ']
            # To be done: add user selected  spw
            firstchanfreq = [ss[0] for ss in chfr]
            lastchanfreq = [ss[-1] for ss in chfr]
            chanwidth = [(ss[-1] - ss[0]) / (len(ss) - 1) for ss in chfr]
            firstchanfreq_all.append(firstchanfreq), chanw_all.append(
                chanwidth), lastchanfreq_all.append(lastchanfreq)
        meerkathi.log.info('CHAN_FREQ from {0:s} Hz to {1:s} Hz with average channel width of {2:s} Hz'.format(
            ','.join(map(str, firstchanfreq)), ','.join(map(str, lastchanfreq)), ','.join(map(str, chanwidth))))

        with open(msinfo, 'r') as stdr:
            tinfo = yaml.safe_load(stdr)['FIELD']
            targetpos = tinfo['REFERENCE_DIR']
            while len(targetpos) == 1:
                targetpos = targetpos[0]
            tRA = targetpos[0] / np.pi * 180.
            tDec = targetpos[1] / np.pi * 180.
            RA.append(tRA)
            Dec.append(tDec)
        meerkathi.log.info(
            'Target RA, Dec for Doppler correction: {0:.3f} deg, {1:.3f} deg'.format(
                RA[i], Dec[i]))

    # Find common barycentric frequency grid for all input .MS, or set it as
    # requested in the config file
    if pipeline.enable_task(config, 'mstransform') and config['mstransform'].get(
            'doppler') and config['mstransform'].get('outchangrid') == 'auto':
        firstchanfreq = list(itertools.chain.from_iterable(firstchanfreq_all))
        chanw = list(itertools.chain.from_iterable(chanw_all))
        lastchanfreq = list(itertools.chain.from_iterable(lastchanfreq_all))
        teldict = {
            'meerkat': [21.4430, -30.7130],
            'gmrt': [73.9723, 19.1174],
            'vla': [-107.6183633, 34.0783584],
            'wsrt': [52.908829698, 6.601997592],
            'atca': [-30.307665436, 149.550164466],
            'askap': [116.5333, -16.9833],
        }
        tellocation = teldict[config["mstransform"].get('telescope')]
        telloc = EarthLocation.from_geodetic(tellocation[0], tellocation[1])
        firstchanfreq_dopp, chanw_dopp, lastchanfreq_dopp = firstchanfreq, chanw, lastchanfreq
        corr_order = False
        if len(chanw) > 1:
            if np.max(chanw) > 0 and np.min(chanw) < 0:
                corr_order = True

        for i, msfile in enumerate(all_msfiles):
            msinfo = '{0:s}/{1:s}-obsinfo.txt'.format(
                pipeline.output, msfile[:-3])
            with open(msinfo, 'r') as searchfile:
                for longdatexp in searchfile:
                    if "Observed from" in longdatexp:
                        dates = longdatexp
                        matches = re.findall(
                            r'(\d{2}[- ](\d{2}|January|Jan|February|Feb|March|Mar|April|Apr|May|May|June|Jun|July|Jul|August|Aug|September|Sep|October|Oct|November|Nov|December|Dec)[\- ]\d{2,4})',
                            dates)
                        obsstartdate = str(matches[0][0])
                        obsdate = datetime.datetime.strptime(
                            obsstartdate, '%d-%b-%Y').strftime('%Y-%m-%d')
                        targetpos = SkyCoord(
                            RA[i], Dec[i], frame='icrs', unit='deg')
                        v = targetpos.radial_velocity_correction(
                            kind='barycentric', obstime=Time(obsdate), location=telloc).to('km/s')
                        corr = np.sqrt((constants.c - v) / (constants.c + v))
                        if corr_order:
                            if chanw_dopp[0] > 0.:
                                firstchanfreq_dopp[i], chanw_dopp[i], lastchanfreq_dopp[i] = lastchanfreq_dopp[i] * \
                                    corr, chanw_dopp[i] * corr, firstchanfreq_dopp[i] * corr
                            else:
                                firstchanfreq_dopp[i], chanw_dopp[i], lastchanfreq_dopp[i] = firstchanfreq_dopp[i] * \
                                    corr, chanw_dopp[i] * corr, lastchanfreq_dopp[i] * corr
                        else:
                            firstchanfreq_dopp[i], chanw_dopp[i], lastchanfreq_dopp[i] = firstchanfreq_dopp[i] * \
                                corr, chanw_dopp[i] * corr, lastchanfreq_dopp[i] * corr  # Hz, Hz, Hz

        # WARNING: the following line assumes a single SPW for the line data
        # being processed by this worker!
        if np.min(chanw_dopp) < 0:
            comfreq0, comfreql, comchanw = np.min(firstchanfreq_dopp), np.max(
                lastchanfreq_dopp), -1 * np.max(np.abs(chanw_dopp))
            # safety measure to avoid wrong Doppler settings due to change of
            # Doppler correction during a day
            comfreq0 += comchanw
            # safety measure to avoid wrong Doppler settings due to change of
            # Doppler correction during a day
            comfreql -= comchanw
        else:
            comfreq0, comfreql, comchanw = np.max(firstchanfreq_dopp), np.min(
                lastchanfreq_dopp), np.max(chanw_dopp)
            # safety measure to avoid wrong Doppler settings due to change of
            # Doppler correction during a day
            comfreq0 += comchanw
            # safety measure to avoid wrong Doppler settings due to change of
            # Doppler correction during a day
            comfreql -= comchanw
        nchan_dopp = int(np.floor(((comfreql - comfreq0) / comchanw))) + 1
        comfreq0 = '{0:.3f}Hz'.format(comfreq0)
        comchanw = '{0:.3f}Hz'.format(comchanw)
        meerkathi.log.info(
            'Calculated common Doppler-corrected channel grid for all input .MS: {0:d} channels starting at {1:s} and with channel width {2:s}.'.format(
                nchan_dopp, comfreq0, comchanw))
        if pipeline.enable_task(config, 'make_image') and config['make_image'].get('image_with')=='wsclean' and corr_order:
            meerkathi.log.info(
                'wsclean will not work when the input measurement sets are ordered in different directions. Use casa_image')
            sys.exit(1)

    elif pipeline.enable_task(config, 'mstransform') and config['mstransform'].get('doppler') and config['mstransform'].get('outchangrid') != 'auto':
        if len(config['mstransform']['outchangrid'].split(',')) != 3:
            meerkathi.log.error(
                'Wrong format for mstransform:outchangrid in the .yml config file.')
            meerkathi.log.error(
                'Current setting is mstransform:outchangrid:"{0:s}"'.format(
                    config['mstransform']['outchangrid']))
            meerkathi.log.error(
                'It must be "nchan,chan0,chanw" (note the commas) where nchan is an integer, and chan0 and chanw must include units appropriate for the chosen mstransform:mode')
            sys.exit(1)
        nchan_dopp, comfreq0, comchanw = config['mstransform']['outchangrid'].split(
            ',')
        nchan_dopp = int(nchan_dopp)
        meerkathi.log.info(
            'Set requested Doppler-corrected channel grid for all input .MS: {0:d} channels starting at {1:s} and with channel width {2:s}.'.format(
                nchan_dopp, comfreq0, comchanw))

    elif pipeline.enable_task(config, 'mstransform'):
        nchan_dopp, comfreq0, comchanw = None, None, None

    for i, msname in enumerate(all_msfiles):
        msname_mst = msname.replace('.ms', '_mst.ms')
        if pipeline.enable_task(config, 'subtractmodelcol'):
            step = 'modelsub_{:d}'.format(i)
            recipe.add('cab/msutils', step,
                       {
                           "command": 'sumcols',
                           "msname": msname,
                           "subtract": True,
                           "col1": 'CORRECTED_DATA',
                           "col2": 'MODEL_DATA',
                           "column": 'CORRECTED_DATA'
                       },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{0:s}:: Subtract model column'.format(step))

        if pipeline.enable_task(config, 'mstransform'):
            if os.path.exists(
                    '{1:s}/{0:s}'.format(msname_mst, pipeline.msdir)):
                os.system(
                    'rm -r {1:s}/{0:s}'.format(msname_mst, pipeline.msdir))
            col = config['mstransform'].get('column')
            step = 'mstransform_{:d}'.format(i)
            recipe.add('cab/casa_mstransform',
                       step,
                       {"msname": msname,
                        "outputvis": msname_mst,
                        "regridms": config['mstransform'].get('doppler'),
                        "mode": config['mstransform'].get('mode'),
                        "nchan": sdm.dismissable(nchan_dopp),
                        "start": sdm.dismissable(comfreq0),
                        "width": sdm.dismissable(comchanw),
                        "interpolation": 'nearest',
                        "datacolumn": col,
                        "restfreq": restfreq,
                        "outframe": config['mstransform'].get('outframe'),
                        "veltype": config['mstransform'].get('veltype'),
                        "douvcontsub": config['mstransform'].get('uvlin'),
                        "fitspw": sdm.dismissable(config['mstransform'].get('fitspw')),
                        "fitorder": config['mstransform'].get('fitorder'),
                        },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{0:s}:: Doppler tracking corrections'.format(step))

            if config['mstransform'].get('obsinfo', True):
                step = 'listobs_{:d}'.format(i)
                recipe.add('cab/casa_listobs',
                           step,
                           {"vis": msname_mst,
                            "listfile": '{0:s}-obsinfo.txt'.format(msname_mst[:-3]),
                            "overwrite": True,
                            },
                           input=pipeline.input,
                           output=pipeline.output,
                           label='{0:s}:: Get observation information ms={1:s}'.format(step,
                                                                                       msname_mst))

                step = 'summary_json_{:d}'.format(i)
                recipe.add(
                    'cab/msutils',
                    step,
                    {
                        "msname": msname_mst,
                        "command": 'summary',
                        "display": False,
                        "outfile": '{0:s}-obsinfo.json'.format(msname_mst[:-3]),
                    },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Get observation information as a json file ms={1:s}'.format(
                        step,
                        msname_mst))

        if pipeline.enable_task(config, 'sunblocker'):
            if config['sunblocker'].get('use_mstransform', True):
                msnamesb = msname_mst
            else:
                msnamesb = msname
            step = 'sunblocker_{0:d}'.format(i)
            prefix = pipeline.prefix[i]
            recipe.add("cab/sunblocker", step,
                       {
                           "command": "phazer",
                           "inset": msnamesb,
                           "outset": msnamesb,
                           "imsize": config['sunblocker'].get('imsize'),
                           "cell": config['sunblocker'].get('cell'),
                           "pol": 'i',
                           "threshmode": 'fit',
                           "threshold": config['sunblocker'].get('threshold'),
                           "mode": 'all',
                           "radrange": 0,
                           "angle": 0,
                           "show": prefix + '.sunblocker.svg',
                           "verb": True,
                           "dryrun": False,
                           "uvmax": config['sunblocker'].get('uvmax'),
                           "uvmin": config['sunblocker'].get('uvmin'),
                           "vampirisms": config['sunblocker'].get('vampirisms'),
                       },
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{0:s}:: Block out sun'.format(step))

        recipe.run()
        recipe.jobs = []
        # Move the sunblocker plots to the diagnostic_plots
        if pipeline.enable_task(config, 'sunblocker'):
            sunblocker_plots = glob.glob(
                "{0:s}/{1:s}".format(pipeline.output, '*.svg'))
            for plot in sunblocker_plots:
                shutil.copy(plot, pipeline.diagnostic_plots)
                os.remove(plot)

    if pipeline.enable_task(config, 'make_image') and config['make_image'].get('image_with')=='wsclean':
        nchans_all, specframe_all = [], []
        label = config['label']
        if label != '':
            flabel = '_' + label
        else:
            flabel = label

        if config['make_image'].get('use_mstransform'):
            all_targets, all_msfiles, ms_dict = target_to_msfiles(
                pipeline.target, pipeline.msnames, flabel, True)
            for i, msfile in enumerate(all_msfiles):
                # If channelisation changed during a previous pipeline run
                # as stored in the obsinfo.json file
                if not pipeline.enable_task(config, 'mstransform'):
                    msinfo = '{0:s}/{1:s}-obsinfo.json'.format(
                        pipeline.output, msfile[:-3])
                    meerkathi.log.info(
                        'Updating info from {0:s}'.format(msinfo))
                    with open(msinfo, 'r') as stdr:
                        spw = yaml.load(stdr)['SPW']['NUM_CHAN']
                        nchans = spw
                        nchans_all.append(nchans)
                    meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(
                        len(spw), ','.join(map(str, spw))))

                    # Get first chan, last chan, chan width
                    with open(msinfo, 'r') as stdr:
                        chfr = yaml.load(stdr)['SPW']['CHAN_FREQ']
                        firstchanfreq = [ss[0] for ss in chfr]
                        lastchanfreq = [ss[-1] for ss in chfr]
                        chanwidth = [(ss[-1] - ss[0]) / (len(ss) - 1) for ss in chfr]
                    meerkathi.log.info('CHAN_FREQ from {0:s} Hz to {1:s} Hz with average channel width of {2:s} Hz'.format(
                        ','.join(map(str, firstchanfreq)), ','.join(map(str, lastchanfreq)), ','.join(map(str, chanwidth))))

                    with open(msinfo, 'r') as stdr:
                        specframe = yaml.load(stdr)['SPW']['MEAS_FREQ_REF']
                        specframe_all.append(specframe)
                    meerkathi.log.info(
                        'The spectral reference frame is {0:}'.format(specframe))

                elif config['mstransform'].get('doppler'):
                    nchans_all.append([nchan_dopp for kk in chanw_all[i]])
                    specframe_all.append([{'lsrd': 0, 'lsrk': 1, 'galacto': 2, 'bary': 3, 'geo': 4, 'topo': 5}[
                                         config['mstransform'].get('outframe')] for kk in chanw_all[i]])
        else:
            #all_targets, all_msfiles, ms_dict = target_to_msfiles(pipeline.target,pipeline.msnames,flabel,False)
            msinfo = '{0:s}/{1:s}-obsinfo.json'.format(
                pipeline.output, msfile[:-3])
            with open(msinfo, 'r') as stdr:
                spw = yaml.load(stdr)['SPW']['NUM_CHAN']
                nchans = spw
                nchans_all.append(nchans)
            meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(
                len(spw), ','.join(map(str, spw))))
            with open(msinfo, 'r') as stdr:
                specframe = yaml.load(stdr)['SPW']['MEAS_FREQ_REF']
                specframe_all.append(specframe)
            meerkathi.log.info(
                'The spectral reference frame is {0:}'.format(specframe))

        spwid = config['make_image'].get('spwid')
        nchans = config['make_image'].get('nchans')
        if nchans == 0:
            # Assuming user wants same spw for all msfiles and they have same
            # number of channels
            nchans = nchans_all[0][spwid]
        # Assuming user wants same spw for all msfiles and they have same
        # specframe
        specframe_all = [ss[spwid] for ss in specframe_all][0]
        firstchan = config['make_image'].get('firstchan')
        binchans = config['make_image'].get('binchans')
        channelrange = [firstchan, firstchan + nchans * binchans]
        npix = config['make_image'].get('npix')
        if len(npix) == 1:
            npix = [npix[0], npix[0]]

        # Construct weight specification
        if config['make_image'].get('weight') == 'briggs':
            weight = 'briggs {0:.3f}'.format(
                config['make_image'].get('robust'))
        else:
            weight = config['make_image'].get('weight')
        wscl_niter = config['make_image'].get('wscl_sofia_niter')
        wscl_tol = config['make_image'].get('wscl_sofia_converge')

        line_image_opts = {
            "weight": weight,
            "taper-gaussian": str(config['make_image'].get('taper')),
            "pol": config['make_image'].get('pol'),
            "npix": npix,
            "padding": config['make_image'].get('padding'),
            "scale": config['make_image'].get('cell'),
            "channelsout": nchans,
            "channelrange": channelrange,
            "niter": config['make_image'].get('niter'),
            "mgain": config['make_image'].get('wscl_mgain'),
            "auto-threshold": config['make_image'].get('wscl_auto_threshold'),
            "multiscale": config['make_image'].get('wscl_multi_scale'),
            "multiscale-scales": sdm.dismissable(config['make_image'].get('wscl_multi_scale_scales')),
            "no-update-model-required": config['make_image'].get('wscl_no_update_mod')
        }

        for target in (all_targets):
            mslist = ms_dict[target]
            field = utils.filter_name(target)
            line_clean_mask_file = None
            rms_values=[]
            for j in range(1, wscl_niter + 1):
                image_path = "{0:s}/image_{1:d}".format(
                    pipeline.cubes, j)
                if not os.path.exists(image_path):
                    os.mkdir(image_path)
                img_dir = '{0:s}/image_{1:d}'.format(
                    get_dir_path(pipeline.cubes, pipeline), j)

                line_image_opts.update({
                    "msname": mslist,
                    "prefix": '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}'.format(
                        img_dir,pipeline.prefix, field, line_name, j)
                    })

                if j == 1:
                    own_line_clean_mask = config['make_image'].get(
                        'wscl_user_clean_mask')
                    if own_line_clean_mask:
                        line_image_opts.update({"fitsmask": '{0:s}/{1:s}:output'.format(
                            get_dir_path(pipeline.masking, pipeline), own_line_clean_mask)})
                        step = 'make_image_{0:s}_{1:d}_with_user_mask'.format(line_name, j)
                    else:
                        line_image_opts.update({"auto-mask": config['make_image'].get('wscl_auto_mask')})
                        step = 'make_image_{0:s}_{1:d}_with_automasking'.format(line_name, j)
                    
                else:
                    step = 'make_sofia_mask_' + str(j - 1)
                    line_clean_mask = '{0:s}_{1:s}_{2:s}_{3:d}.image_clean_mask.fits:output'.format(
                        pipeline.prefix, field, line_name, j)
                    line_clean_mask_file = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.image_clean_mask.fits'.format(
                        image_path, pipeline.prefix, field, line_name, j)
                    cubename = '{0:s}_{1:s}_{2:s}_{3:d}.image.fits:input'.format(
                        pipeline.prefix, field, line_name, j - 1)
                    cubename_file = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.image.fits'.format(
                        image_path, pipeline.prefix, field, line_name, j - 1)
                    outmask = '{0:s}_{1:s}_{2:s}_{3:d}.image_clean'.format(
                        pipeline.prefix, field, line_name, j)
                    recipe.add('cab/sofia', step,
                               {
                                   "import.inFile": cubename,
                                   "steps.doFlag": False,
                                   "steps.doScaleNoise": True,
                                   "steps.doSCfind": True,
                                   "steps.doMerge": True,
                                   "steps.doReliability": False,
                                   "steps.doParameterise": False,
                                   "steps.doWriteMask": True,
                                   "steps.doMom0": False,
                                   "steps.doMom1": False,
                                   "steps.doWriteCat": False,
                                   "flag.regions": [],
                                   "scaleNoise.statistic": 'mad',
                                   "SCfind.threshold": 4,
                                   "SCfind.rmsMode": 'mad',
                                   "merge.radiusX": 3,
                                   "merge.radiusY": 3,
                                   "merge.radiusZ": 3,
                                   "merge.minSizeX": 2,
                                   "merge.minSizeY": 2,
                                   "merge.minSizeZ": 2,
                                   "writeCat.basename": outmask,
                               },
                               input=pipeline.cubes + '/image_' + str(j - 1),
                               output=pipeline.output + '/' + img_dir,
                               label='{0:s}:: Make SoFiA mask'.format(step))

                    recipe.run()
                    recipe.jobs = []

                    if not os.path.exists(line_clean_mask_file):
                        meerkathi.log.info(
                            'Sofia mask_' + str(j - 1) + ' was not found. Exiting and saving the cube')
                        j -= 1
                        break

                    step = 'make_image_{0:s}_{1:d}_with_SoFiA_mask'.format(line_name, j)
                    line_image_opts.update({"fitsmask": '{0:s}/{1:s}'.format(img_dir, line_clean_mask)})
                    if 'auto-mask' in line_image_opts:
                        del(line_image_opts['auto-mask'])
                    
                recipe.add('cab/wsclean',
                           step, line_image_opts,
                           input=pipeline.input,
                           output=pipeline.output,
                           label='{:s}:: Image Line'.format(step))
                recipe.run()
                recipe.jobs = []

                # delete line "MFS" images made by WSclean by averaging all channels
                for mfs in glob.glob('{0:s}/{1:s}/{2:s}_{3:s}_{4:s}_{5:d}-MFS*fits'.format(
                    pipeline.output,img_dir,pipeline.prefix, field, line_name, j)):
                    os.remove(mfs)

                # Stack channels together into cubes and fix spectral frame
                if config['make_image']['wscl_make_cube']:
                    if not config['make_image'].get('niter'):
                        imagetype = ['dirty', 'image']
                    else:
                        imagetype = ['dirty', 'image', 'psf', 'residual', 'model']
                        if config['make_image'].get('wscl_mgain') < 1.0:
                            imagetype.append('first-residual')
                    for mm in imagetype:
                        step = 'make_{0:s}_cube'.format(
                            mm.replace('-', '_'))
                        if not os.path.exists('{6:s}/{0:s}/{1:s}_{2:s}_{3:s}_{4:d}-0000-{5:s}.fits'.format(
                                img_dir, pipeline.prefix, field, line_name, j, mm, pipeline.output)):
                            meerkathi.log.info('Skipping container {0:s}. Single channels do not exist.'.format(step))
                        else:
                            stacked_cube = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.{5:s}.fits'.format(img_dir,
                                            pipeline.prefix, field, line_name, j, mm)
                            recipe.add(
                                'cab/fitstool',
                                step,
                                {
                                    "image": ['{0:s}/{1:s}_{2:s}_{3:s}_{4:d}-{5:04d}-{6:s}.fits:output'.format(
                                            img_dir, pipeline.prefix, field, line_name,
                                            j, d, mm) for d in range(nchans)],
                                    "output": stacked_cube,
                                    "stack": True,
                                    "delete-files": True,
                                    "fits-axis": 'FREQ',
                                },
                                input=pipeline.input,
                                output=pipeline.output,
                                label='{0:s}:: Make {1:s} cube from wsclean {1:s} channels'.format(
                                    step,
                                    mm.replace('-', '_')))

                            recipe.run()
                            recipe.jobs = []

                            # Replace channels that are single-valued (usually zero-ed) in the dirty cube with blanks
                            #   in all cubes assuming that channels run along numpy axis 1 (axis 0 is for Stokes)
                            with fits.open('{0:s}/{1:s}'.format(pipeline.output, stacked_cube)) as stck:
                                cubedata=stck[0].data
                                cubehead=stck[0].header
                                if mm == 'dirty':
                                    tobeblanked = (cubedata == np.nanmean(cubedata,axis = (0, 2, 3)).reshape((
                                        1, cubedata.shape[1], 1, 1))).all(axis = (0, 2, 3))
                                cubedata[:, tobeblanked] = np.nan
                                fits.writeto('{0:s}/{1:s}'.format(pipeline.output, stacked_cube), cubedata, header = cubehead, overwrite = True)

                    for ss in ['dirty', 'psf', 'first-residual', 'residual', 'model', 'image']:
                        cubename = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.{5:s}.fits:output'.format(
                            img_dir, pipeline.prefix, field, line_name, j, ss)
                        recipe.add(fix_specsys,
                                   'fix_specsys_{0:s}_cube'.format(ss),
                                   {'filename': cubename,
                                       'specframe': specframe_all,
                                    },
                                   input=pipeline.input,
                                   output=pipeline.output,
                                   label='Fix spectral reference frame for cube {0:s}'.format(cubename))
                    recipe.run()
                    recipe.jobs = []

                cubename_file = '{0:s}/image_{1:d}/{2:s}_{3:s}_{4:s}_{1:d}.image.fits'.format(
                    pipeline.cubes, j, pipeline.prefix, field, line_name)
                rms_values.append(calc_rms(cubename_file, line_clean_mask_file))
                meerkathi.log.info('RMS = {0:.3e} Jy/beam for {1:s}'.format(rms_values[-1],cubename_file))

                # if the RMS has decreased by a factor < wscl_tol compared to the previous cube then cleaning is no longer improving the cube and we can stop
                if len(rms_values) > 1 and wscl_tol and rms_values[-2] / rms_values[-1] <= wscl_tol:
                    meerkathi.log.info('The cube RMS noise has decreased by a factor <= {0:.3f} compared to the previous WSclean iteration. Noise convergence achieved.'.format(wscl_tol))
                    break

                # If the RMS has decreased by a factor > wscl_tol compared to the previous cube then cleaning is still improving the cube and it's worth continuing with a new SoFiA + WSclean iteration
                elif len(rms_values) > 1 and wscl_tol and rms_values[-2] / rms_values[-1] > wscl_tol :
                    #rms_old = rms_new
                    meerkathi.log.info('The cube RMS noise has decreased by a factor > {0:.3f} compared to the previous WSclean iteration. The noise has not converged yet and we should continue iterating SoFiA + WSclean.'.format(wscl_tol))
                    if j == wscl_niter:
                        meerkathi.log.info('Stopping anyway. Maximum number of SoFiA + WSclean iterations reached.')
                    else:
                        meerkathi.log.info('Starting a new SoFiA + WSclean iteration.')
                    
            # Out of SoFiA + WSclean loop -- prepare final data products
            for ss in ['dirty', 'psf', 'first-residual', 'residual', 'model', 'image']:
                if 'dirty' in ss:
                    meerkathi.log.info('Preparing final cubes.')
                cubename = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.{5:s}.fits'.format(
                    image_path, pipeline.prefix, field, line_name, j, ss)
                finalcubename = '{0:s}/{1:s}_{2:s}_{3:s}.{4:s}.fits'.format(
                    image_path, pipeline.prefix, field, line_name, ss)
                line_clean_mask_file = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.image_clean_mask.fits'.format(
                    image_path, pipeline.prefix, field, line_name, j)
                final_line_clean_mask_file = '{0:s}/{1:s}_{2:s}_{3:s}.image_clean_mask.fits'.format(
                    image_path, pipeline.prefix, field, line_name)
                MFScubename = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}-MFS-{5:s}.fits'.format(
                    image_path, pipeline.prefix, field, line_name, j, ss)
                finalMFScubename = '{0:s}/{1:s}_{2:s}_{3:s}-MFS-{4:s}.fits'.format(
                    image_path, pipeline.prefix, field, line_name, ss)
                if os.path.exists(cubename):
                    os.rename(cubename, finalcubename)
                if os.path.exists(line_clean_mask_file):
                    os.rename(line_clean_mask_file, final_line_clean_mask_file)
                if os.path.exists(MFScubename):
                    os.rename(MFScubename, finalMFScubename)

            for j in range(1, wscl_niter):
                if config['make_image'].get('wscl_keep_final_products_only'):
                    for ss in ['dirty', 'psf', 'first-residual', 'residual', 'model', 'image']:
                        cubename = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.{5:s}.fits'.format(
                            pipeline.cubes, pipeline.prefix, field, line_name, j, ss)
                        line_clean_mask_file = '{0:s}/{1:s}_{2:s}_{3:s}_{4:d}.image_clean_mask.fits'.format(
                            pipeline.cubes, pipeline.prefix, field, line_name, j)
                        MFScubename = '{0:s}/{1:s}_{2:s}_{3:s}_{4:s}-MFS-{5:s}.fits'.format(
                            pipeline.cubes, pipeline.prefix, field, line_name, j, ss)
                        if os.path.exists(cubename):
                            os.remove(cubename)
                        if os.path.exists(line_clean_mask_file):
                            os.remove(line_clean_mask_file)
                        if os.path.exists(MFScubename):
                            os.remove(MFScubename)

    if pipeline.enable_task(config, 'make_image') and config['make_image'].get('image_with')=='casa':
        img_dir = get_dir_path(pipeline.cubes, pipeline)
        nchans_all, specframe_all = [], []
        label = config['label']
        if label != '':
            flabel = '_' + label
        else:
            flabel = label
        if config['make_image'].get('use_mstransform'):
            all_targets, all_msfiles, ms_dict = target_to_msfiles(
                pipeline.target, pipeline.msnames, flabel, True)
            for i, msfile in enumerate(all_msfiles):
                if not pipeline.enable_task(config, 'mstransform'):
                    msinfo = '{0:s}/{1:s}-obsinfo.json'.format(
                        pipeline.output, msfile[:-3])
                    meerkathi.log.info(
                        'Updating info from {0:s}'.format(msinfo))
                    with open(msinfo, 'r') as stdr:
                        spw = yaml.load(stdr)['SPW']['NUM_CHAN']
                        nchans = spw
                        nchans_all.append(nchans)
                    meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(
                        len(spw), ','.join(map(str, spw))))

                    # Get first chan, last chan, chan width
                    with open(msinfo, 'r') as stdr:
                        chfr = yaml.load(stdr)['SPW']['CHAN_FREQ']
                        firstchanfreq = [ss[0] for ss in chfr]
                        lastchanfreq = [ss[-1] for ss in chfr]
                        chanwidth = [(ss[-1] - ss[0]) / (len(ss) - 1)
                                     for ss in chfr]
                    meerkathi.log.info('CHAN_FREQ from {0:s} Hz to {1:s} Hz with average channel width of {2:s} Hz'.format(
                        ','.join(map(str, firstchanfreq)), ','.join(map(str, lastchanfreq)), ','.join(map(str, chanwidth))))

                    with open(msinfo, 'r') as stdr:
                        specframe = yaml.load(stdr)['SPW']['MEAS_FREQ_REF']
                        specframe_all.append(specframe)
                    meerkathi.log.info(
                        'The spectral reference frame is {0:}'.format(specframe))

                elif config['mstransform'].get('doppler'):
                    nchans_all[i] = [nchan_dopp for kk in chanw_all[i]]
                    specframe_all.append([{'lsrd': 0, 'lsrk': 1, 'galacto': 2, 'bary': 3, 'geo': 4, 'topo': 5}[
                                         config['mstransform'].get('outframe', 'bary')] for kk in chanw_all[i]])
        else:
            msinfo = '{0:s}/{1:s}-obsinfo.json'.format(
                pipeline.output, msfile[:-3])
            with open(msinfo, 'r') as stdr:
                spw = yaml.load(stdr)['SPW']['NUM_CHAN']
                nchans = spw
                nchans_all.append(nchans)
            meerkathi.log.info('MS has {0:d} spectral windows, with NCHAN={1:s}'.format(
                len(spw), ','.join(map(str, spw))))
            with open(msinfo, 'r') as stdr:
                specframe = yaml.load(stdr)['SPW']['MEAS_FREQ_REF']
                specframe_all.append(specframe)
            meerkathi.log.info(
                'The spectral reference frame is {0:}'.format(specframe))

        spwid = config['make_image'].get('spwid')
        nchans = config['make_image'].get('nchans')
        if nchans == 0:
            # Assuming user wants same spw for all msfiles and they have same
            # number of channels
            nchans = nchans_all[0][spwid]
        # Assuming user wants same spw for all msfiles and they have same
        # specframe
        specframe_all = [ss[spwid] for ss in specframe_all][0]
        firstchan = config['make_image'].get('firstchan')
        binchans = config['make_image'].get('binchans')
        channelrange = [firstchan, firstchan + nchans * binchans]
        # Construct weight specification
        if config['make_image'].get('weight') == 'briggs':
            weight = 'briggs {0:.3f}'.format(
                config['make_image'].get('robust', robust))
        else:
            weight = config['make_image'].get('weight', weight)

        for target in (all_targets):
            mslist = ms_dict[target]
            field = utils.filter_name(target)

            step = 'make_image_line'
            image_opts = {
                "msname": mslist,
                "prefix": '{0:s}/{1:s}_{2:s}_{3:s}'.format(img_dir, pipeline.prefix, field, line_name),
                "mode": 'channel',
                "nchan": nchans,
                "start": config['make_image'].get('firstchan'),
                "interpolation": 'nearest',
                "niter": config['make_image'].get('niter'),
                "psfmode": 'hogbom',
                "threshold": config['make_image'].get('casa_threshold'),
                "npix": config['make_image'].get('npix'),
                "cellsize": config['make_image'].get('cell'),
                "weight": config['make_image'].get('weight'),
                "robust": config['make_image'].get('robust'),
                "stokes": config['make_image'].get('pol'),
                "port2fits": config['make_image'].get('casa_port2fits'),
                "restfreq": restfreq,
            }
            if config['make_image'].get('taper') != '':
                image_opts.update({
                    "uvtaper": True,
                    "outertaper": config['make_image'].get('taper'),
                })
            recipe.add('cab/casa_clean', step, image_opts,
                       input=pipeline.input,
                       output=pipeline.output,
                       label='{:s}:: Image Line'.format(step))

    recipe.run()
    recipe.jobs = []
    
    # Once all cubes have been made fix the headers etc.
    # Search img_dir and img_dir/images_*/ for cubes whose header should be fixed
    img_dir = get_dir_path(pipeline.cubes, pipeline)
    for target in all_targets:
        mslist = ms_dict[target]
        field = utils.filter_name(target)

        casa_cube_list=glob.glob('{0:s}/{1:s}/{2:s}_{3:s}_{4:s}*.fits'.format(
            pipeline.output,img_dir, pipeline.prefix, field, line_name))
        wscl_cube_list=glob.glob('{0:s}/{1:s}/image_*/{2:s}_{3:s}_{4:s}*.fits'.format(
            pipeline.output,img_dir, pipeline.prefix, field, line_name))
        # rm first occurrence of pipeline.output in cube file names
        cube_list = [''.join(cc.split(pipeline.output+'/')[1:]) for cc in casa_cube_list+wscl_cube_list]
        image_cube_list = [cc for cc in cube_list if 'image.fits' in cc]
        
        if pipeline.enable_task(config, 'remove_stokes_axis'):
            for uu in range(len(cube_list)):
                recipe.add(remove_stokes_axis,
                           'remove_cube_stokes_axis_{0:d}'.format(uu),
                           {'filename': cube_list[uu]+':output',
                            },
                           input=pipeline.input,
                           output=pipeline.output,
                           label='Remove Stokes axis for cube {0:s}'.format(cube_list[uu]))

        if pipeline.enable_task(config, 'pb_cube'):
            for uu in range(len(image_cube_list)):
                recipe.add(make_pb_cube, 'make pb_cube_{0:d}'.format(uu),
                       {'filename': image_cube_list[uu]+':output',
                        'apply_corr': config['pb_cube'].get('apply_pb')},
                       input=pipeline.input,
                       output=pipeline.output,
                       label='Make primary beam cube for {0:s}'.format(image_cube_list[uu]))

        if pipeline.enable_task(config, 'freq_to_vel'):
            if not config['freq_to_vel'].get('reverse'):
                meerkathi.log.info(
                    'Converting spectral axis of cubes from frequency to radio velocity')
            else:
                meerkathi.log.info(
                    'Converting spectral axis of cubes from radio velocity to frequency')
            for uu in range(len(cube_list)):
                recipe.add(freq_to_vel,
                    'spectral_header_to_vel_radio_cube_{0:d}'.format(uu),
                    {
                        'filename': cube_list[uu]+':output',
                        'reverse': config['freq_to_vel'].get('reverse')},
                    input=pipeline.input,
                    output=pipeline.output,
                    label='Convert spectral axis from frequency to radio velocity for cube {0:s}'.format(cube_list[uu]))

        recipe.run()
        recipe.jobs = []

        if pipeline.enable_task(config, 'sofia'):
            for uu in range(len(image_cube_list)):
                step = 'sofia_source_finding_{0:d}'.format(uu)
                recipe.add(
                    'cab/sofia',
                    step,
                    {
                        "import.inFile": image_cube_list[uu].split('/')[-1]+':input',
                        "steps.doFlag": config['sofia'].get('flag'),
                        "steps.doScaleNoise": True,
                        "steps.doSCfind": True,
                        "steps.doMerge": config['sofia'].get('merge'),
                        "steps.doReliability": False,
                        "steps.doParameterise": False,
                        "steps.doWriteMask": True,
                        "steps.doMom0": config['sofia'].get('do_mom0'),
                        "steps.doMom1": config['sofia'].get('do_mom1'),
                        "steps.doCubelets": config['sofia'].get('do_cubelets'),
                        "steps.doWriteCat": False,
                        "flag.regions": config['sofia'].get('flagregion'),
                        "scaleNoise.statistic": config['sofia'].get('rmsMode'),
                        "SCfind.threshold": config['sofia'].get('threshold'),
                        "SCfind.rmsMode": config['sofia'].get('rmsMode'),
                        "merge.radiusX": config['sofia'].get('mergeX'),
                        "merge.radiusY": config['sofia'].get('mergeY'),
                        "merge.radiusZ": config['sofia'].get('mergeZ'),
                        "merge.minSizeX": config['sofia'].get('minSizeX'),
                        "merge.minSizeY": config['sofia'].get('minSizeY'),
                        "merge.minSizeZ": config['sofia'].get('minSizeZ'),
                    },
                    input='/'.join('{0:s}/{1:s}'.format(pipeline.output,image_cube_list[uu]).split('/')[:-1]),
                    output='/'.join('{0:s}/{1:s}'.format(pipeline.output,image_cube_list[uu]).split('/')[:-1]),
                    label='{0:s}:: Make SoFiA mask and images for cube {1:s}'.format(step,image_cube_list[uu]))

        if pipeline.enable_task(config, 'sharpener'):
            for uu in range(len(image_cube_list)):
                step = 'continuum_spectral_extraction_{0:d}'.format(uu)
    
                params = {"enable_spec_ex": True,
                          "enable_source_catalog": True,
                          "enable_abs_plot": True,
                          "enable_source_finder": False,
                          "cubename": image_cube_list[uu]+':output',
                          "channels_per_plot": config['sharpener'].get('channels_per_plot'),
                          "workdir": '{0:s}/'.format(stimela.CONT_IO[recipe.JOB_TYPE]["output"]),
                          "label": config['sharpener'].get('label', pipeline.prefix)
                          }

                if config['sharpener'].get('catalog') == 'PYBDSF':
                    catalogs = []
                    nimages = glob.glob("{0:s}/image_*".format(pipeline.continuum))
    
                    for ii in range(0, len(nimages)):
                        catalog = glob.glob("{0:s}/image_{1:d}/{2:s}_{3:s}_*.lsm.html".format(
                                pipeline.continuum, ii + 1, pipeline.prefix, field))
                        catalogs.append(catalog)
    
                    catalogs = sorted(catalogs)
                    catalogs = [cat for catalogs in catalogs for cat in catalogs]
                    # Right now, this is the last catalog made
                    catalog_file = catalogs[-1].split('output/')[-1]
                    params["catalog_file"] = '{0:s}:output'.format(catalog_file)
    
                    if len(catalog_file) > 0:
    
                        params["catalog"] = "PYBDSF"
                        recipe.add('cab/sharpener',
                            step,
                            params,
                            input=pipeline.input,
                            output=pipeline.output,
                            label='{0:s}:: Continuum Spectral Extraction'.format(step))
                    else:
                        meerkathi.log.info(
                            'No PyBDSM catalogs found. Skipping continuum spectral extraction.')

                elif config['sharpener'].get('catalog') == 'NVSS':
                    params["thresh"] = config['sharpener'].get('thresh')
                    params["width"] = config['sharpener'].get('width')
                    params["catalog"] = "NVSS"
                    recipe.add('cab/sharpener',
                        step,
                        params,
                        input=pipeline.input,
                        output=pipeline.output,
                        label='{0:s}:: Continuum Spectral Extraction'.format(step))

                recipe.run()
                recipe.jobs = []
    
                # Move the sharpener output to diagnostic_plots
                sharpOut = '{0:s}/{1:s}'.format(pipeline.output, 'sharpOut')
                finalsharpOut = '{0:s}/{1:s}_{2:s}_{3:s}'.format(
                    pipeline.diagnostic_plots, pipeline.prefix, field, 'sharpOut')
                if os.path.exists(finalsharpOut):
                    shutil.rmtree(finalsharpOut)
                shutil.move(sharpOut, finalsharpOut)