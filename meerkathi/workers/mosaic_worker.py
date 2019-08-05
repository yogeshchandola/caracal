import os
import sys

NAME = "Mosaic images output by selfcal or imageHI"

def worker(pipeline, recipe, config):

    wname = pipeline.CURRENT_WORKER

    # Parameters acquired from the config file   ### How do we get them from the schema instead?
    specified_mosaictype = config['mosaic_type'] # i.e. 'continuum' or 'spectral'
    specified_cutoff = config['cutoff'] # e.g. 0.25
    
    # Empty list to add filenames to
    specified_images = []

    # Parameters relevant to there being multiple images per run of the pipeline
    for i in range(pipeline.nobs):

        msname = pipeline.msnames[i]
        prefix = pipeline.prefixes[i]
        mfsprefix = ["", '-MFS'][int(nchans>1)]
        ### Is the following correct to do?
        field = pipeline.fields[i]
        num = pipeline.nums[i]

        # Use the mosaictype to infer the filenames of the images
        if specified_mosaictype = 'continuum':  # Add name of 2D image output by selfcal
            image_name = '{0:s}_{1:s}_{2:d}{3:s}-image.fits'.format(prefix, field, num, mfsprefix)
            specified_images = specified_images.append(image_name)
        else:  # i.e. mosaictype = 'spectral', so add name of cube output by imageHI
            image_name = '{0:s}_{1:s}_HI_{2:d}.image.fits'.format(prefix, field, num)
            specified_images = specified_images.append(image_name)

    # List of images in place now, so ready to add montage_mosaic to the meerkathi recipe
    if pipeline.enable_task(config, 'domontage'):
        recipe.add('cab/montage_mosaic', 'montage_mosaic',
            {
                "mosaic-type"    : specified_mosaictype,
                "domontage"      : True,
                "cutoff"         : specified_cutoff,
                "name"           : prefix,
                "target-images"  : specified_images,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='montage_mosaic:: Re-gridding {0:s} images before mosaicking them'.format(specified_mosaictype))
    else:
        recipe.add('cab/montage_mosaic', 'montage_mosaic',
            {
                "mosaic-type"    : specified_mosaictype,
                "domontage"      : False,
                "cutoff"         : specified_cutoff,
                "name"           : prefix,
                "target-images"  : specified_images,
            },
            input=pipeline.input,
            output=pipeline.output,
            label='montage_mosaic:: Re-gridding already done, so straight to mosaicking {0:s} images'.format(specified_mosaictype))
     
    ### Leaving the following as a reminder of syntax    
    #if pipeline.enable_task(config, 'add_spectral_weights'):
        #step = 'estimate_weights_{:d}'.format(i)
        #recipe.add('cab/msutils', step,
        #    {
        #      "msname"          : msname,
        #      "command"         : 'estimate_weights',
        #      "stats_data"      : config['add_spectral_weights'].get('stats_data', 'use_package_meerkat_spec'),
        #      "weight_columns"  : config['add_spectral_weights'].get('weight_columns', ['WEIGHT', 'WEIGHT_SPECTRUM']),
        #      "noise_columns"   : config['add_spectral_weights'].get('noise_columns', ['SIGMA', 'SIGMA_SPECTRUM']),
        #      "write_to_ms"     : config['add_spectral_weights'].get('write_to_ms', True),
        #      "plot_stats"      : prefix + '-noise_weights.png',
        #    },
        #    input=pipeline.input,
        #    output=pipeline.output,
        #    label='{0:s}:: Adding Spectral weights using MeerKAT noise specs ms={1:s}'.format(step, msname))


