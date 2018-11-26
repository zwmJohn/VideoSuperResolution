"""
Copyright: Wenyi Tang 2017-2018
Author: Wenyi Tang
Email: wenyi.tang@intel.com
Created Date: July 20th 2018

Conventional Generator and Discriminator as well as objective function
for generative adversarial networks 
"""

import tensorflow as tf
import numpy as np
from functools import partial

_INCEPTION_BATCH = 50


def _preprocess_for_inception(images):
    """Preprocess images for inception.

    Args:
      images: images minibatch. Shape [batch size, width, height,
        channels]. Values are in [0..255].

    Returns:
      preprocessed_images
    """

    images = tf.cast(images, tf.float32)

    # tfgan_eval.preprocess_image function takes values in [0, 255]
    with tf.control_dependencies([tf.assert_greater_equal(images, 0.0),
                                  tf.assert_less_equal(images, 255.0)]):
        images = tf.identity(images)

    preprocessed_images = tf.map_fn(
        fn=tf.contrib.gan.eval.preprocess_image,
        elems=images,
        back_prop=False)

    return preprocessed_images


def _run_inception(images, layer_name, inception_graph):
    preprocessed = _preprocess_for_inception(images)
    return tf.contrib.gan.eval.run_inception(preprocessed,
                                             output_tensor=layer_name,
                                             graph_def=inception_graph)


def fid_score(real_image, gen_image, num_batches=None):
    """FID function from tf.contrib

    Args:
        real_image: must be 4-D tensor, ranges from [0, 255]
        gen_image: must be 4-D tensor, ranges from [0, 255]
        num_batches: Number of batches to split `generated_images` in to in
          order to efficiently run them through the classifier network.
    """
    batches = real_image.shape[0]
    assert gen_image.shape[0] == batches
    if not num_batches:
        num_batches = (batches + _INCEPTION_BATCH - 1) // _INCEPTION_BATCH
    graph = tf.contrib.gan.eval.get_graph_def_from_url_tarball(
        'http://download.tensorflow.org/models/frozen_inception_v1_2015_12_05.tar.gz',
        'inceptionv1_for_inception_score.pb',
        '/tmp/frozen_inception_v1_2015_12_05.tar.gz')
    # make tensor batches
    real_data = tf.data.Dataset.from_tensors(real_image).batch(_INCEPTION_BATCH)
    gen_data = tf.data.Dataset.from_tensors(gen_image).batch(_INCEPTION_BATCH)
    real_iter = real_data.make_one_shot_iterator().get_next()
    gen_iter = gen_data.make_one_shot_iterator().get_next()
    real_features = _run_inception(real_iter, 'pool_3:0', graph)
    gen_features = _run_inception(gen_iter, 'pool_3:0', graph)
    sess = tf.get_default_session()
    real_feature_np = []
    gen_feature_np = []
    for _ in range(num_batches):
        r, g = sess.run([real_features, gen_features])
        real_feature_np.append(r)
        gen_feature_np.append(g)
    real_feature_np = np.concatenate(real_feature_np)
    gen_feature_np = np.concatenate(gen_feature_np)
    fid_tensor = tf.contrib.gan.eval.frechet_classifier_distance(
        classifier_fn=tf.identity,
        real_images=real_feature_np,
        generated_images=gen_feature_np,
        num_batches=num_batches)
    return fid_tensor


def inception_score(images, num_batches=None):
    """IS function from tf.contrib

    Args:
        images: must be 4-D tensor, ranges from [0, 255]
        num_batches: Number of batches to split `generated_images` in to in
          order to efficiently run them through the classifier network.
    """
    batches = images.shape[0]
    if not num_batches:
        num_batches = (batches + _INCEPTION_BATCH - 1) // _INCEPTION_BATCH
    graph = tf.contrib.gan.eval.get_graph_def_from_url_tarball(
        'http://download.tensorflow.org/models/frozen_inception_v1_2015_12_05.tar.gz',
        'inceptionv1_for_inception_score.pb',
        '/tmp/frozen_inception_v1_2015_12_05.tar.gz')
    return tf.contrib.gan.eval.classifier_score(
        images=images,
        classifier_fn=partial(_run_inception,
                              layer_name='logits:0',
                              inception_graph=graph),
        num_batches=num_batches)


def loss_bce_gan(y_real, y_fake):
    """Original GAN loss with BCE"""

    d_loss = tf.losses.sigmoid_cross_entropy(tf.ones_like(y_real), y_real) + \
             tf.losses.sigmoid_cross_entropy(tf.zeros_like(y_fake), y_fake)

    g_loss = tf.losses.sigmoid_cross_entropy(tf.ones_like(y_fake), y_fake)
    return g_loss, d_loss


def loss_relative_bce_gan(y_real, y_fake, average=False):
    """R(A)GAN"""
    bce = tf.losses.sigmoid_cross_entropy
    if average:
        d_loss = bce(tf.ones_like(y_real), y_real - tf.reduce_mean(y_fake)) + \
                 bce(tf.zeros_like(y_fake), y_fake - tf.reduce_mean(y_real))

        g_loss = bce(tf.ones_like(y_fake), y_fake - tf.reduce_mean(y_real)) + \
                 bce(tf.zeros_like(y_real), y_real - tf.reduce_mean(y_fake))
    else:
        d_loss = bce(tf.ones_like(y_real), y_real - y_fake) + \
                 bce(tf.zeros_like(y_fake), y_fake - y_real)

        g_loss = bce(tf.ones_like(y_fake), y_fake - y_real)
    return g_loss, d_loss


def loss_wgan(y_real, y_fake):
    """W-GAN"""

    d_loss = tf.reduce_mean(y_fake - y_real)
    g_loss = -tf.reduce_mean(y_fake)

    return g_loss, d_loss


def gradient_penalty(y_true, y_pred, graph_fn, lamb=10):
    """Gradient penalty"""

    if not callable(graph_fn):
        raise TypeError('graph callee is not a callable!')

    diff = y_pred - y_true
    alpha = tf.random_uniform(tf.shape(diff)[0], minval=0., maxval=1.)
    alpha = tf.reshape(alpha, [-1, 1, 1, 1])
    interp = y_true + alpha * diff
    gradients = tf.gradients(graph_fn(interp), [interp])[0]
    slopes = tf.sqrt(1e-4 + tf.reduce_sum(
        tf.square(gradients), reduction_indices=[1, 2, 3]))
    gp = tf.reduce_mean(tf.square(slopes - 1.))
    return lamb * gp


def loss_lsgan(y_real, y_fake):
    """Least-Square GAN"""

    d_loss = tf.reduce_mean((y_real - 1) ** 2) + tf.reduce_mean(y_fake ** 2)
    g_loss = tf.reduce_mean((y_fake - 1) ** 2)
    return g_loss * 0.5, d_loss * 0.5


def loss_relative_lsgan(y_real, y_fake, average=False):
    """R(A)LSGAN"""

    if average:
        d_loss = tf.reduce_mean((y_real - tf.reduce_mean(y_fake) - 1) ** 2) + \
                 tf.reduce_mean((y_fake - tf.reduce_mean(y_real) + 1) ** 2)

        g_loss = tf.reduce_mean((y_real - tf.reduce_mean(y_fake) + 1) ** 2) + \
                 tf.reduce_mean((y_fake - tf.reduce_mean(y_real) - 1) ** 2)
    else:
        d_loss = tf.reduce_mean((y_real - y_fake - 1) ** 2)
        g_loss = tf.reduce_mean((y_fake - y_real - 1) ** 2)
    return g_loss, d_loss


def loss_sensitive_gan(y_real, y_fake):
    raise NotImplementedError
