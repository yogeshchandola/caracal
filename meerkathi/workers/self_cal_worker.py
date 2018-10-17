import os, shutil, glob
import sys
import yaml
import json
import os
import meerkathi
import stimela.dismissable as sdm
from meerkathi.dispatch_crew import utils
from astropy.io import fits as fits

NAME = 'Self calibration loop'

# self_cal_iter_counter is used as a global variable.


CUBICAL_OUT = {
    "CORR_DATA"    : 'sc',
    "CORR_RES"     : 'sr',
}

CUBICAL_MT = {
    "Gain2x2"      : 'complex-2x2',
    "GainDiag"      : 'complex-2x2',  #TODO:: Change this. Ask cubical to support this mode
    "GainDiagPhase": 'phase-diag',
}


def worker(pipeline, recipe, config):
    npix = config['img_npix']
    trim = config['img_trim']
    spwid = config.get('spwid', 0)
    cell = config['img_cell']
    mgain = config['img_mgain']
    niter = config['img_niter']
    robust = config['img_robust']
    nchans = config['img_nchans']
    pol = config.get('img_pol', 'I')
    joinchannels = config['img_joinchannels']
    fit_spectral_pol = config['img_fit_spectral_pol']
    bsols = config.get('cal_Bsols', [])
    taper = config.get('img_uvtaper', None)
    label = config['label']
    bjones = config.get('cal_Bjones', False)
    time_chunk = config.get('cal_time_chunk', 128)
    ncpu = config.get('ncpu', 9)
    mfsprefix = ["", '-MFS'][int(nchans>1)]
    cal_niter = config.get('cal_niter', 1)

    pipeline.set_cal_msnames(label)
    mslist = pipeline.cal_msnames
    prefix = pipeline.prefix

    # Define image() extract_sources() calibrate()
    # functions for convience

    def cleanup_files(mask_name):

      if os.path.exists(pipeline.output+'/'+mask_name):
          shutil.move(pipeline.output+'/'+mask_name,pipeline.output+'/masking/'+mask_name)

      casafiles = glob.glob(pipeline.output+'/*.image')
      for i in xrange(0,len(casafiles)):
        shutil.rmtree(casafiles[i])


    def change_header(filename,headfile,copy_head):
      pblist = fits.open(filename)

      dat = pblist[0].data

      if copy_head == True:
        hdrfile = fits.open(headfile)
        head = hdrfile[0].header
      elif copy_head == False:

        head = pblist[0].header

        if 'ORIGIN' in head:
          del head['ORIGIN']
        if 'CUNIT1' in head:
          del head['CUNIT1']
        if 'CUNIT2' in head:
          del head['CUNIT2']

      fits.writeto(filename,dat,head,overwrite=True)


    def image(num):
        key = 'image'
        mask = False
        if config[key].get('peak_based_mask_on_dirty', False):
            mask = True
            step = 'image_{}_dirty'.format(num)
            recipe.add('cab/wsclean', step,
                  {
                      "msname"    : mslist,
                      "column"    : config[key].get('column', "CORRECTED_DATA")[num-1 if len(config[key].get('column')) >= num else -1],
                      "weight"    : 'briggs {}'.format(config.get('robust', robust)),
                      "npix"      : config[key].get('npix', npix),
                      "trim"      : config[key].get('trim', trim),
                      "scale"     : config[key].get('cell', cell),
                      "pol"       : config[key].get('pol', pol),
                      "channelsout"   : nchans,
                      "taper-gaussian" : sdm.dismissable(config[key].get('uvtaper', taper)),
                      "prefix"    : '{0:s}_{1:d}'.format(prefix, num),
                  },
            input=pipeline.input,
            output=pipeline.output,
            label='{:s}:: Make dirty image to create clean mask'.format(step))

            step = 'mask_dirty_{}'.format(num)
            recipe.add('cab/cleanmask', step,
               {
                 "image"           :  '{0:s}_{1:d}{2:s}-image.fits:output'.format(prefix, num, mfsprefix),
                 "output"          :  '{0:s}_{1:d}-mask.fits'.format(prefix, num),
                 "dilate"          :  False,
                 "peak-fraction"   :  0.5,
                 "no-negative"     :  True,
                 "boxes"           :  1,
                 "log-level"       :  'DEBUG',
               },
               input=pipeline.input,
               output=pipeline.output,
               label='{0:s}:: Make mask based on peak of dirty image'.format(step))

        elif config[key].get('mask', False):
            mask = True
            sigma = config[key].get('mask_sigma', None)
            pf = config[key].get('mask_peak_fraction', None)
            step = 'mask_{}'.format(num)
            recipe.add('cab/cleanmask', step,
               {
                 "image"           :  '{0:s}_{1:d}{2:s}-image.fits:output'.format(prefix, num-1, mfsprefix),
                 "output"          :  '{0:s}_{1:d}-mask.fits'.format(prefix, num),
                 "dilate"          :  False,
                 "peak-fraction"   :  sdm.dismissable(pf),
                 "sigma"           :  sdm.dismissable(sigma),
                 "no-negative"     :  True,
                 "boxes"           :  1,
                 "log-level"       :  'DEBUG',
               },
               input=pipeline.input,
               output=pipeline.output,
               label='{0:s}:: Make mask based on peak of dirty image'.format(step))

        step = 'image_{}'.format(num)
        image_opts = {
                  "msname"    : mslist,
                  "column"    : config[key].get('column', "CORRECTED_DATA")[num-1 if len(config[key].get('column')) >= num else -1],
                  "weight"    : 'briggs {}'.format(config[key].get('robust', robust)),
                  "npix"      : config[key].get('npix', npix),
                  "trim"      : config[key].get('trim', trim),
                  "scale"     : config[key].get('cell', cell),
                  "prefix"    : '{0:s}_{1:d}'.format(prefix, num),
                  "niter"     : config[key].get('niter', niter),
                  "mgain"     : config[key].get('mgain', mgain),
                  "pol"       : config[key].get('pol', pol),
                  "taper-gaussian" : sdm.dismissable(config[key].get('uvtaper', taper)),
                  "channelsout"     : nchans,
                  "joinchannels"    : config[key].get('joinchannels', joinchannels),
                  "fit-spectral-pol": config[key].get('fit_spectral_pol', fit_spectral_pol),
                  "auto-threshold": config[key].get('auto_threshold',[])[num-1 if len(config[key].get('auto_threshold', [])) >= num else -1],
                  "multiscale" : config[key].get('multi_scale', False),
                  "multiscale-scales" : sdm.dismissable(config[key].get('multi_scale_scales', None)),
              }
        if config[key].get('mask_from_sky', False):
            fitmask = config[key].get('fits_mask', None)
            fitmask_address = 'masking/'+str(fitmask)
            image_opts.update( {"fitsmask" : fitmask_address+':output'})
        elif mask:
            image_opts.update( {"fitsmask" : '{0:s}_{1:d}-mask.fits:output'.format(prefix, num)} )
        else:
            image_opts.update({"auto-mask" : config[key].get('auto_mask',[])[num-1 if len(config[key].get('auto_mask', [])) >= num else -1]})

        recipe.add('cab/wsclean', step,
        image_opts,
        input=pipeline.input,
        output=pipeline.output,
        label='{:s}:: Make image after first round of calibration'.format(step))

    def sofia_mask(num):
        step = 'make_sofia_mask'
        key = 'sofia_mask'
        imagename = '{0:s}_{1:d}-MFS-image.fits'.format(prefix, num)
        def_kernels = [[0, 0, 0, 'b'], [3, 3, 0, 'b'], [6, 6, 0, 'b'], [15, 15, 0, 'b']]
   
        # user_kern = config[key].get('kernels', None)
        # if user_kern:
        #   for i in xrange(0,len(user_kern))
        #     kern. 
        #     def_kernels.concatenate(config[key].get('kernels'))

        image_opts =   {
              "import.inFile"         : imagename,
              "steps.doFlag"          : True,
              "steps.doScaleNoise"    : True,
              "steps.doSCfind"        : True,
              "steps.doMerge"         : True,
              "steps.doReliability"   : False,
              "steps.doParameterise"  : False,
              "steps.doWriteMask"     : True,
              "steps.doMom0"          : False,
              "steps.doMom1"          : False,
              "steps.doWriteCat"      : False, 
              "SCfind.kernelUnit"     : 'pixel',
              "SCfind.kernels"        : def_kernels,
              "SCfind.threshold"      : config.get('threshold',4), 
              "SCfind.rmsMode"        : 'mad',
              "SCfind.edgeMode"       : 'constant',
              "SCfind.fluxRange"      : 'all',
              "scaleNoise.statistic"  : 'mad' ,
              "scaleNoise.method"     : 'local',
              "scaleNoise.scaleX"     : True,
              "scaleNoise.scaleY"     : True,
              "scaleNoise.scaleZ"     : False,
              "merge.radiusX"         : 3, 
              "merge.radiusY"         : 3,
              "merge.radiusZ"         : 1,
              "merge.minSizeX"        : 2,
              "merge.minSizeY"        : 2, 
              "merge.minSizeZ"        : 1,
            }
        if config[key].get('flag') :
          flags_sof = config[key].get('flagregion')
          image_opts.update({"flag.regions": flags_sof})
        
        if config[key].get('inputmask') :
          #change header of inputmask so it is the same as image
          mask_name = 'masking/'+config[key].get('inputmask')
          
          mask_name_casa = mask_name.split('.fits')[0]
          mask_name_casa = mask_name_casa+'.image'

          mask_regrid_casa = mask_name_casa+'_regrid.image'
          
          imagename_casa = '{0:s}_{1:d}{2:s}-image.image'.format(prefix, num, mfsprefix)

          recipe.add('cab/casa_importfits', step,
            {
              "fitsimage"         : imagename+':output',
              "imagename"         : imagename_casa,
              "overwrite"         : True,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='Image in casa format')

          recipe.add('cab/casa_importfits', step,
            {
              "fitsimage"         : mask_name+':output',
              "imagename"         : mask_name_casa,
              "overwrite"         : True,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='Mask in casa format')
          
          step = '3'
          recipe.add('cab/casa_imregrid', step,
            {
              "template"      : imagename_casa+':output',
              "imagename"     : mask_name_casa+':output',
              "output"        : mask_regrid_casa,
              "overwrite"     : True,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='Regridding mosaic to size and projection of dirty image')

          step = '4'
          recipe.add('cab/casa_exportfits', step,
            {
              "fitsimage"         : mask_name+':output',
              "imagename"         : mask_regrid_casa+':output',
              "overwrite"         : True,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='Extracted regridded mosaic')
          
          step = '5'
          recipe.add(change_header,step,
            {
              "filename"  : pipeline.output+'/'+mask_name,
              "headfile"  : pipeline.output+'/'+imagename,
              "copy_head" : True,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='Extracted regridded mosaic')

          image_opts.update({"import.maskFile": mask_name})
          image_opts.update({"import.inFile": imagename})
        
        recipe.add('cab/sofia', step,
          image_opts,
          input=pipeline.output,
          output=pipeline.output+'/masking/',
          label='{0:s}:: Make SoFiA mask'.format(step))
        

        # step = '7'
        # name_sof_out = imagename.split('.fits')[0]
        # name_sof_out = name_sof_out+'_mask.fits'

        # recipe.add(cleanup_files, step,
        #   {
        #     'mask_name' : name_sof_out
        #   },
        #   input=pipeline.input,
        #   output=pipeline.output,
        #   label='{0:s}:: Make SoFiA mask'.format(step))


    def make_cube(num, imtype='model'):
        im = '{0:s}_{1}-cube.fits:output'.format(prefix, num)
        step = 'makecube_{}'.format(num)
        images = ['{0:s}_{1}-{2:04d}-{3:s}.fits:output'.format(prefix, num, i, imtype) for i in range(nchans)]
        recipe.add('cab/fitstool', step,
            {
                "image"     : images,
                "output"    : im,
                "stack"     : True,
                "fits-axis" : 'FREQ',
            },
            input=pipeline.input,
            output=pipeline.output,
            label='{0:s}:: Make convolved model'.format(step))

        return im

    def extract_sources(num):
        key = 'extract_sources'
        if config[key].get('detection_image', False):
            step = 'detection_image_{0:d}'.format(num)
            detection_image = prefix + '-detection_image_{0:d}.fits:output'.format(num)
            recipe.add('cab/fitstool', step,
                {
                    "image"    : [prefix+'_{0:d}{2:s}-{1:s}.fits:output'.format(num, im, mfsprefix) for im in ('image','residual')],
                    "output"   : detection_image,
                    "diff"     : True,
                    "force"    : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Make convolved model'.format(step))
        else:
            detection_image = None

        spi_do = config[key].get('spi', False)
        if spi_do:
            im = make_cube(num, 'image')
        else:
            im = '{0:s}_{1:d}{2:s}-image.fits:output'.format(prefix, num, mfsprefix)

        step = 'extract_{0:d}'.format(num)
        calmodel = '{0:s}_{1:d}-pybdsm'.format(prefix, num)
        if detection_image:
            blank_limit = 1e-9
        else:
            blank_limit = None

        recipe.add('cab/pybdsm', step,
            {
                "image"         : im,
                "thresh_pix"    : config[key].get('thresh_pix', [])[num-1 if len(config[key].get('thresh_pix')) >= num else -1],
                "thresh_isl"    : config[key].get('thresh_isl', [])[num-1 if len(config[key].get('thresh_isl')) >= num else -1],
                "outfile"       : '{:s}.fits:output'.format(calmodel),
                "blank_limit"   : sdm.dismissable(blank_limit),
                "adaptive_rms_box" : True,
                "port2tigger"   : True,
                "multi_chan_beam": spi_do,
                "spectralindex_do": spi_do,
                "detection_image": sdm.dismissable(detection_image),
            },
            input=pipeline.input,
            output=pipeline.output,
            label='{0:s}:: Extract sources'.format(step))

    def predict_from_fits(num, model, index):
        if isinstance(model, str) and len(model.split('+'))==2:
            combine = True
            mm = model.split('+')
            # Combine FITS models if more than one is given
            step = 'combine_models_' + '_'.join(map(str, mm))
            calmodel = '{0:s}_{1:d}-FITS-combined.fits:output'.format(prefix, num)
            cubes = [ make_cube(n, 'model') for n in mm]
            recipe.add('cab/fitstool', step,
                {
                    "image"    : cubes,
                    "output"   : calmodel,
                    "sum"      : True,
                    "force"    : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Add clean components'.format(step))
        else:
            calmodel = make_cube(num)

        step = 'predict_fromfits_{}'.format(num)
        recipe.add('cab/lwimager', 'predict', {
                "msname"        : mslist[index],
                "simulate_fits" : calmodel,
                "column"        : 'MODEL_DATA',
                "img_nchan"     : nchans,
                "img_chanstep"  : 1,
                "nchan"         : pipeline.nchans[index], #TODO: This should consider SPW IDs
                "cellsize"      : cell,
                "chanstep"      : 1,
        },
            input=pipeline.input,
            output=pipeline.output,
            label='{0:s}:: Predict from FITS ms={1:s}'.format(step, mslist[index]))


    def combine_models(models, num, enable=True):
        model_names = ['{0:s}_{1:s}-pybdsm.lsm.html:output'.format(
                       prefix, m) for m in models]
        model_names_fits = ['{0:s}/{1:s}_{2:s}-pybdsm.fits'.format(
                            pipeline.output, prefix, m) for m in models]
        calmodel = '{0:s}_{1:d}-pybdsm-combined.lsm.html:output'.format(prefix, num)

        if enable:
            step = 'combine_models_' + '_'.join(map(str, models))
            recipe.add('cab/tigger_convert', step,
                {
                    "input-skymodel"    : model_names[0],
                    "append"    : model_names[1],
                    "output-skymodel"   : calmodel,
                    "rename"  : True,
                    "force"   : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Combined models'.format(step))

        return calmodel, model_names_fits


    def calibrate_meqtrees(num):
        key = 'calibrate'
        
        if num == cal_niter:
            vismodel = config[key].get('add_vis_model', False)  
        else:
            vismodel = False
        #force to calibrate with model data column if specified by user

        if config[key].get('model_mode', None) == 'pybdsm_vis':
            vismodel = True
            calmodel = '{0:s}_{1:d}-nullmodel.txt'.format(prefix, num)
            model = config[key].get('model', num)[num-1]
            with open(os.path.join(pipeline.input, calmodel), 'w') as stdw:
                stdw.write('#format: ra_d dec_d i\n')
                stdw.write('0.0 -30.0 1e-99')
            for i, msname in enumerate(mslist):
                predict_from_fits(num, model, i)

            modelcolumn = 'MODEL_DATA'
        
        elif config[key].get('model_mode', None) == 'pybdsm_only':
            model = config[key].get('model', num)[num-1]
            if isinstance(model, str) and len(model.split('+')) > 1:
                mm = model.split('+')
                calmodel, fits_model = combine_models(mm, num,
                                           enable=False if pipeline.enable_task(
                                           config, 'aimfast') else True)
            else:
                model = int(model)
                calmodel = '{0:s}_{1:d}-pybdsm.lsm.html:output'.format(prefix, model)
                fits_model = '{0:s}/{1:s}_{2:d}-pybdsm.fits'.format(pipeline.output, prefix, model)

            modelcolumn = ''
        
        elif  config[key].get('model_mode', None) == 'vis_only':
            vismodel = True
            modelcolumn = 'MODEL_DATA'
            calmodel = '{0:s}_{1:d}-nullmodel.txt'.format(prefix, num)
            with open(os.path.join(pipeline.input, calmodel), 'w') as stdw:
                stdw.write('#format: ra_d dec_d i\n')
                stdw.write('0.0 -30.0 1e-99')

        for i,msname in enumerate(mslist):
            gsols_ = [config[key].get('Gsols_time',[])[num-1 if num <= len(config[key].get('Gsols_time',[])) else -1],
                          config[key].get('Gsols_channel', [])[num-1 if num <= len(config[key].get('Gsols_channel',[])) else -1]]
            bsols_ = config[key].get('Bsols', bsols)
            step = 'calibrate_{0:d}_{1:d}'.format(num, i)
            recipe.add('cab/calibrator', step,
               {
                 "skymodel"             : calmodel,  #in case I don't want to use a sky model
                 "add-vis-model"        : vismodel,
                 "model-column"         : modelcolumn,
                 "msname"               : msname,
                 "threads"              : ncpu,
                 "column"               : "DATA",
                 "output-data"          : config[key].get('output_data', 'CORR_DATA')[num-1 if len(config[key].get('output_data')) >= num else -1],
                 "output-column"        : "CORRECTED_DATA",
                 "prefix"               : '{0:s}-{1:d}_meqtrees'.format(pipeline.dataid[i], num),
                 "label"                : 'cal{0:d}'.format(num),
                 "read-flags-from-ms"   : True,
                 "read-flagsets"        : "-stefcal",
                 "write-flagset"        : "stefcal",
                 "write-flagset-policy" : "replace",
                 "Gjones"               : True,
                 "Gjones-solution-intervals" : sdm.dismissable(gsols_ or None),
                 "Gjones-matrix-type"   : config[key].get('gain_matrix_type', 'GainDiag')[num-1 if len(config[key].get('gain_matrix_type')) >= num else -1], 
                 "Gjones-ampl-clipping"      : True,
                 "Gjones-ampl-clipping-low"  : config.get('cal_gain_amplitude_clip_low', 0.5),
                 "Gjones-ampl-clipping-high" : config.get('cal_gain_amplitude_clip_high', 1.5),
                 "Bjones"                    : config[key].get('Bjones', False),
                 "Bjones-solution-intervals" : sdm.dismissable(bsols_ or None),
                 "Bjones-ampl-clipping"      : config[key].get('Bjones', bjones),
                 "Bjones-ampl-clipping-low"  : config.get('cal_gain_amplitude_clip_low', 0.5),
                 "Bjones-ampl-clipping-high" : config.get('cal_gain_amplitude_clip_high', 1.5),
                 "make-plots"           : True,
                 "tile-size"            : time_chunk,
               },
               input=pipeline.input,
               output=pipeline.output,
               label="{0:s}:: Calibrate step {1:d} ms={2:s}".format(step, num, msname))

    def calibrate_cubical(num):
        key = 'calibrate'
        model = config[key].get('model', num)
        if isinstance(model, str) and len(model.split('+'))>1:
            mm = model.split('+')
            calmodel, fits_model = combine_models(mm, num)
        else:
            model = int(model)
            calmodel = '{0:s}_{1:d}-pybdsm.lsm.html:output'.format(prefix, model)
            fits_model = '{0:s}/{1:s}_{2:d}-pybdsm.fits'.format(pipeline.output, prefix, model)

        if config[key].get('Bjones', bjones):
            jones_chain = 'G,B'
        else:
            jones_chain = 'G' 

        for i,msname in enumerate(mslist):
            gsols_ = [config[key].get('Gsols_time', [])[num-1 if num <= len(config[key].get('Gsols_time',[])) else -1],
                config[key].get('Gsols_channel', [])[num-1 if num <= len(config[key].get('Gsols_channel',[])) else -1]]
            bsols_ = config[key].get('Bsols', bsols)

            step = 'calibrate_cubical_{0:d}_{1:d}'.format(num, i)
            recipe.add('cab/cubical', step, 
                {   
                    "data-ms"          : msname, 
                    "data-column"      : 'DATA',
                    "model-column"     : 'MODEL_DATA' if config[key].get('add_vis_model', False) else ' "" ',
                    "j2-term-iters"    : 200,
                    "data-time-chunk"  : time_chunk,
                    "sel-ddid"         : sdm.dismissable(config[key].get('spwid', None)),
                    "dist-ncpu"        : ncpu,
                    "sol-jones"        : jones_chain,
                    "model-lsm"        : calmodel,
                    "out-name"         : '{0:s}-{1:d}_cubical'.format(pipeline.dataid[i], num),
                    "out-mode"         : CUBICAL_OUT[config[key].get('output_data', 'CORR_DATA')],
                    "out-plots-show"   : False,
                    "weight-column"    : config[key].get('weight_column', 'WEIGHT'),
                    "montblanc-dtype"  : 'float',
                    "j1-solvable"      : True,
                    "j1-type"          : CUBICAL_MT[config[key].get('gain_matrix_type','Gain2x2')[num-1] if num <= len(config[key].get('gain_matrix_type','Gain2x2')) else 'Gain2x2'],
                    "j1-time-int"      : gsols_[0],
                    "j1-freq-int"      : gsols_[1],
                    "j1-clip-low"      : config.get('cal_gain_amplitude_clip_low', 0.5),
                    "j1-clip-high"     : config.get('cal_gain_amplitude_clip_high', 1.5),
                    "j2-solvable"      : config[key].get('Bjones', bjones),
                    "j2-type"          : CUBICAL_MT[config[key].get('gain_matrix_type', 'Gain2x2')],
                    "j2-time-int"      : bsols_[0],
                    "j2-freq-int"      : bsols_[1],
                    "j2-clip-low"      : config.get('cal_gain_amplitude_clip_low', 0.5),
                    "j2-clip-high"     : config.get('cal_gain_amplitude_clip_high', 1.5),
                },  
                input=pipeline.input,
                output=pipeline.output,
                shared_memory='100Gb',
                label="{0:s}:: Calibrate step {1:d} ms={2:s}".format(step, num, msname))

    def get_aimfast_data(filename='{0:s}/fidelity_results.json'.format(pipeline.output)):
        "Extracts data from the json data file"
        with open(filename) as f:
            data = json.load(f)
        return data

    def quality_check(n, enable=True):
        "Examine the aimfast results to see if they meet specified conditions"
        # If total number of iterations is reached stop

        if enable:
            # The recipe has to be executed at this point to get the image fidelity results
            
            recipe.run()
            # Empty job que after execution
            recipe.jobs = []
            key = 'aimfast'
            tolerance = config[key].get('tolerance', 0.02)
            fidelity_data = get_aimfast_data()
            # Ensure atleast one iteration is ran to compare previous and subsequent images
            if n>= 2:
                conv_crit = config[key].get('convergence_criteria', ["DR", "SKEW", "KURT", "STDDEV", "MEAN"])
                conv_crit= [cc.upper() for cc in conv_crit]
                # Ensure atleast one iteration is ran to compare previous and subsequent images
                residual0=fidelity_data['meerkathi_{0}-residual'.format(n - 1)]
                residual1 = fidelity_data['meerkathi_{0}-residual'.format(n)]
                # Unlike the other ratios DR should grow hence n-1/n < 1.

                if not extractsourcesset:
                    drratio=fidelity_data['meerkathi_{0}-restored'.format(n - 1)]['DR']/fidelity_data[
                                          'meerkathi_{0}-restored'.format(n)]['DR']
                else:
                    drratio=residual0['meerkathi_{0}-model'.format(n - 1)]['DR']/residual1['meerkathi_{0}-model'.format(n)]['DR']

                # Dynamic range is important,
                if any(cc == "DR" for cc in conv_crit):
                    drweight = 0.8
                else:
                    drweight = 0.
                # The other parameters should become smaller, hence n/n-1 < 1
                skewratio=residual1['SKEW']/residual0['SKEW']
                # We care about the skewness when it is large. What is large?
                # Let's go with 0.005 at that point it's weight is 0.5
                if any(cc == "SKEW" for cc in conv_crit):
                    skewweight=residual1['SKEW']/0.01
                else:
                    skewweight = 0.
                kurtratio=residual1['KURT']/residual0['KURT']
                # Kurtosis goes to 3 so this way it counts for 0.5 when normal distribution
                if any(cc == "KURT" for cc in conv_crit):
                    kurtweight=residual1['KURT']/6.
                else:
                    kurtweight = 0.
                meanratio=residual1['MEAN']/residual0['MEAN']
                # We only care about the mean when it is large compared to the noise
                # When it deviates from zero more than 20% of the noise this is a problem
                if any(cc == "MEAN" for cc in conv_crit):
                    meanweight=residual1['MEAN']/(residual1['STDDev']*0.2)
                else:
                    meanweight = 0.
                noiseratio=residual1['STDDev']/residual0['STDDev']
                # The noise should not change if the residuals are gaussian in n-1.
                # However, they should decline in case the residuals are non-gaussian.
                # We want a weight that goes to 0 in both cases
                if any(cc == "STDDEV" for cc in conv_crit):
                    if residual0['KURT']/6. < 0.52 and residual0['SKEW'] < 0.01:
                        noiseweight=abs(1.-noiseratio)
                    else:
                        # If declining then noiseratio is small and that's good, If rising it is a real bad thing.
                        #  Hence we can just square the ratio
                        noiseweight=noiseratio
                else:
                    noiseweight = 0.
                # These weights could be integrated with the ratios however while testing I
                #  kept them separately such that the idea behind them is easy to interpret.
                # This  combines to total weigth of 1.2+0.+0.5+0.+0. so our total should be LT 1.7*(1-tolerance)
                # it needs to be slightly lower to avoid keeping fitting without improvement
                # Ok that is the wrong philosophy. Their weighted mean should be less than 1-tolerance that means improvement.
                # And the weights control how important each parameter is.
                HolisticCheck=(drratio*drweight+skewratio*skewweight+kurtratio*kurtweight+meanratio*meanweight+noiseratio*noiseweight) \
                              /(drweight+skewweight+kurtweight+meanweight+noiseweight)
                if (1 - tolerance) < HolisticCheck:
                    meerkathi.log.info('Stopping criterion: '+' '.join([cc for cc in conv_crit]))
                    meerkathi.log.info('{:f} < {:f}'.format(1-tolerance, HolisticCheck))
                #   If we stop we want change the final output model to the previous iteration
                    global self_cal_iter_counter
                    self_cal_iter_counter -= 1


                    return False
            # If we reach the number of iterations we want to stop.
            if n == cal_niter + 1:
                meerkathi.log.info('Number of iterations reached: {:d}'.format(cal_niter))
                return False
            # If no condition is met return true to continue
        return True

    def image_quality_assessment(num):
        # Check if more than two calibration iterations to combine successive models
        # Combine models <num-1> (or combined) to <num> creat <num+1>-pybdsm-combine
        # This was based on thres_pix but change to model as when extract_sources = True is will take the last settings
        if len(config['calibrate'].get('model', [])) >= num:
            model = config['calibrate'].get('model', num)[num-1]
            if isinstance(model, str) and len(model.split('+'))==2:
                mm = model.split('+')
                combine_models(mm, num)
        # in case we are in the last round, imaging has made a model that is longer then the expected model column
        # Therefore we take this last model if model is not defined
        if num == cal_niter+1:
            try:
                model.split()
            except NameError:
                model = str(num)

        step = 'aimfast'

        aimfast_settings = {
                    "residual-image"       : '{0:s}_{1:d}{2:s}-residual.fits:output'.format(
                                                 prefix, num, mfsprefix),
                    "normality-model"      : config[step].get(
                                                 'normality_model', 'normaltest'),
                    "area-factor"          : config[step].get('area_factor', 10),
                    "label"                : "meerkathi_{}".format(num),
                }
        # if we run pybdsm we want to use the  model as well.
        if extractsourcesset:
            aimfast_settings.update({"tigger-model"   : '{0:s}_{1:d}-pybdsm{2:s}.lsm.html:output'.format(
                prefix, num if num <= len(config['calibrate'].get('model', num))
                else len(config['calibrate'].get('model', num)),
                '-combined' if len(model.split('+')) >= 2 else '')})
        else:
            # Use the image
            if config['calibrate'].get('output_data')[num-1] == "CORR_DATA":
                aimfast_settings.update({"restored-image" : '{0:s}_{1:d}{2:s}-image.fits:output'.format(
                                                                prefix, num, mfsprefix)})
            else:
                try:
                    im = config['calibrate'].get('output_data').index("CORR_RES") + 1
                except ValueError:
                    im = num
                aimfast_settings.update({"restored-image" : '{0:s}_{1:d}{2:s}-image.fits:output'.format(
                                                                prefix, im, mfsprefix)})
        recipe.add('cab/aimfast', step,
            aimfast_settings,
            input=pipeline.output,
            output=pipeline.output,
            label="{0:s}_{1:d}:: Image fidelity assessment for {2:d}".format(step, num, num))

    # decide which tool to use for calibration
    calwith = config.get('calibrate_with', 'meqtrees').lower()
    if calwith == 'meqtrees':
        calibrate = calibrate_meqtrees
    elif calwith == 'cubical':
        calibrate = calibrate_cubical

    # if model_mode is vis only we do not want to run pybdsm
    extractsourcesset = pipeline.enable_task(config, 'extract_sources')
    if config['calibrate'].get('model_mode') == 'vis_only':
        extractsourcesset = False
    # selfcal loop
    global self_cal_iter_counter
    self_cal_iter_counter = config.get('start_at_iter', 1)
    if pipeline.enable_task(config, 'image'):
        image(self_cal_iter_counter)
    if extractsourcesset:
        extract_sources(self_cal_iter_counter)
    if pipeline.enable_task(config, 'aimfast'):
        image_quality_assessment(self_cal_iter_counter)
    while quality_check(self_cal_iter_counter,
                        enable=True if pipeline.enable_task(
                            config, 'aimfast') else False):
        if pipeline.enable_task(config, 'calibrate'):
            calibrate(self_cal_iter_counter)
        self_cal_iter_counter += 1
        if pipeline.enable_task(config, 'image'):
            image(self_cal_iter_counter)
        if extractsourcesset:
            extract_sources(self_cal_iter_counter)
        if pipeline.enable_task(config, 'aimfast'):
            image_quality_assessment(self_cal_iter_counter)

    if pipeline.enable_task(config, 'restore_model'):
        if config['restore_model']['model']:
            num = config['restore_model']['model']
            if isinstance(num, str) and len(num.split('+')) == 2:
                mm = num.split('+')
                if int(mm[-1]) > self_cal_iter_counter:
                    num = str(self_cal_iter_counter)
        else:
            extract_sources = len(config['extract_sources'].get(
                                  'thresh_isl', [self_cal_iter_counter]))
            if extract_sources > 1:
                num = '{:d}+{:d}'.format(self_cal_iter_counter-1, self_cal_iter_counter)
            else:
                num = self_cal_iter_counter

        if isinstance(num, str) and len(num.split('+')) == 2:
            mm = num.split('+')
            models = ['{0:s}_{1:s}-pybdsm.lsm.html:output'.format(
                      prefix, m) for m in mm]
            final = '{0:s}_final-pybdsm.lsm.html:output'.format(prefix)

            step = 'create_final_lsm_{0:s}_{1:s}'.format(*mm)
            recipe.add('cab/tigger_convert', step,
                {
                    "input-skymodel"    : models[0],
                    "append"            : models[1],
                    "output-skymodel"   : final,
                    "rename"            : True,
                    "force"             : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Combined models'.format(step))

        elif isinstance(num, str) and num.isdigit():
            inputlsm = '{0:s}_{1:s}-pybdsm.lsm.html:output'.format(prefix, num)
            final = '{0:s}_final-pybdsm.lsm.html:output'.format(prefix)
            step = 'create_final_lsm_{0:s}'.format(num)
            recipe.add('cab/tigger_convert', step,
                {
                    "input-skymodel"    : inputlsm,
                    "output-skymodel"   : final,
                    "rename"  : True,
                    "force"   : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Combined models'.format(step))
        else:
            raise ValueError("restore_model_model should be integer-valued string or indicate which models to be appended, eg. 2+3")

        if config['restore_model'].get('clean_model', None):
            num = int(config['restore_model'].get('clean_model', None))
            if num > self_cal_iter_counter:
                num = self_cal_iter_counter

            conv_model = prefix + '-convolved_model.fits:output'
            recipe.add('cab/fitstool', step,
                {
                    "image"    : [prefix+'_{0:d}{2:s}-{1:s}.fits:output'.format(num, im, mfsprefix) for im in ('image','residual')],
                    "output"   : conv_model,
                    "diff"     : True,
                    "force"    : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Make convolved model'.format(step))

            with_cc = prefix + '-with_cc.fits:output'
            recipe.add('cab/fitstool', step,
                {
                    "image"    : [prefix+'_{0:d}{1:s}-image.fits:output'.format(num, mfsprefix), conv_model],
                    "output"   : with_cc,
                    "sum"      : True,
                    "force"    : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Add clean components'.format(step))

            recipe.add('cab/tigger_restore', step,
                {
                    "input-image"    : with_cc,
                    "input-skymodel" : final,
                    "output-image"   : prefix+'.fullrest.fits',
                    "force"          : True,
                },
                input=pipeline.input,
                output=pipeline.output,
                label='{0:s}:: Add extracted skymodel'.format(step))

        for i,msname in enumerate(mslist):
            if pipeline.enable_task(config, 'flagging_summary'):
                step = 'flagging_summary_image_selfcal_{0:d}'.format(i)
                recipe.add('cab/casa_flagdata', step,
                    {
                      "vis"         : msname,
                      "mode"        : 'summary',
                    },
                    input=pipeline.input,
                    output=pipeline.output,
                    label='{0:s}:: Flagging summary  ms={1:s}'.format(step, msname))
