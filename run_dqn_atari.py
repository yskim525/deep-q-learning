"""

Usage:
    run_dqn_atari.py [options]

Options:
    --batch-size=<size>                     Batch size [default: 32]
    --envid=<envid>                         Environment id [default: SpaceInvadersNoFrameskip-v4]
    --model=(atari|simple|fesimple|random)  Model to use for training [default: atari]
    --num-filters=<num>                     Number of output filters for simple model [default: 64]
    --timesteps=<steps>                     Number of timesteps to run [default: 40000000]
    --restore=<store>                       Checkpoint to restore network from
    --ckpt-dir=<dir>                        Directory contain checkpoint files [default: ./checkpoints]
    --learning-starts=<start>               Timestep when learning starts [default: 200000]
"""

import docopt
import dqn
import gym
import time
import os
import os.path as osp
import random
import numpy as np
import tensorflow as tf
import tensorflow.contrib.layers as layers

from atari_wrappers import *
from dqn_utils import *
from gym import wrappers
from tensorflow.contrib.layers.python.layers import initializers


def atari_model(img_in, num_actions, scope, reuse=False):
    # as described in https://storage.googleapis.com/deepmind-data/assets/papers/DeepMindNature14236Paper.pdf
    with tf.variable_scope(scope, reuse=reuse):
        out = img_in
        with tf.variable_scope("convnet"):
            # original architecture
            out = layers.convolution2d(out, num_outputs=32, kernel_size=8, stride=4, activation_fn=tf.nn.relu)
            out = layers.convolution2d(out, num_outputs=64, kernel_size=4, stride=2, activation_fn=tf.nn.relu)
            out = layers.convolution2d(out, num_outputs=64, kernel_size=3, stride=1, activation_fn=tf.nn.relu)
        out = layers.flatten(out)
        with tf.variable_scope("action_value"):
            out = layers.fully_connected(out, num_outputs=512,         activation_fn=tf.nn.relu)
            out = layers.fully_connected(out, num_outputs=num_actions, activation_fn=None)

        return out


def simple_model(img_in, num_actions, scope, reuse=False, num_filters=64):
    with tf.variable_scope(scope, reuse=reuse):
        out = img_in
        gauss_initializer = initializers.xavier_initializer(uniform=False)  # stddev = 1/n
        with tf.variable_scope("convnet"):
            out = layers.convolution2d(
                out, num_outputs=num_filters, kernel_size=8, stride=4,
                activation_fn=tf.nn.relu, weights_initializer=gauss_initializer,
                trainable=False)
        out = layers.flatten(out)
        with tf.variable_scope("action_value"):
            out = layers.fully_connected(out, num_outputs=num_actions, activation_fn=None)

        return out


def simple_model_w_feat_eng(img_in, num_actions, scope, reuse=False):
    with tf.variable_scope(scope, reuse=reuse):
        out = img_in
        out = layers.flatten(out)
        # stddev = 1/n, where n = number of inputs
        gauss_initializer = initializers.xavier_initializer(uniform=False)
        with tf.variable_scope("action_value"):
            out = layers.fully_connected(
                out,
                num_outputs=num_actions,
                activation_fn=tf.nn.relu,
                biases_initializer=None,
                weights_initializer=gauss_initializer,
                weights_regularizer=None)
        return out


def atari_learn(env,
                session,
                num_timesteps,
                model,
                restore=None,
                checkpoint_dir='./checkpoints',
                batch_size=32,
                num_filters=64,
                learning_starts=200000):
    # This is just a rough estimate
    num_iterations = float(num_timesteps) / 4.0
    learning_starts = int(learning_starts) / 4.0

    lr_multiplier = 1.0
    lr_schedule = PiecewiseSchedule([
                                         (0,                   1e-4 * lr_multiplier),
                                         (num_iterations / 10, 1e-4 * lr_multiplier),
                                         (num_iterations / 2,  5e-5 * lr_multiplier),
                                    ],
                                    outside_value=5e-5 * lr_multiplier)

    if model == 'fesimple':
        optimizer = dqn.OptimizerSpec(
            constructor=tf.train.GradientDescentOptimizer,
            kwargs=dict(),
            lr_schedule=lr_schedule
        )
    else:
        optimizer = dqn.OptimizerSpec(
            constructor=tf.train.AdamOptimizer,
            kwargs=dict(epsilon=1e-4),
            lr_schedule=lr_schedule
        )

    def stopping_criterion(env, t):
        # notice that here t is the number of steps of the wrapped env,
        # which is different from the number of steps in the underlying env
        return get_wrapper_by_name(env, "Monitor").get_total_steps() >= num_timesteps

    exploration_schedule = PiecewiseSchedule(
        [
            (0, 1.0),
            (1e6, 0.1),
            (num_iterations / 2 if num_iterations > 1e6 else 1e9, 0.01),
        ], outside_value=0.01
    )

    if model == 'atari':
        q_func = atari_model
    elif model =='fesimple':
        q_func = simple_model_w_feat_eng
    else:
        q_func = lambda *args, **kwargs:\
            simple_model(*args, num_filters=num_filters, **kwargs)

    save_path = dqn.learn(
        env,
        q_func=q_func,
        optimizer_spec=optimizer,
        session=session,
        exploration=exploration_schedule,
        stopping_criterion=stopping_criterion,
        replay_buffer_size=1000000,
        batch_size=batch_size,
        gamma=0.99,
        learning_starts=learning_starts,
        learning_freq=4,
        frame_history_len=4,
        target_update_freq=10000,
        grad_norm_clipping=10,
        restore=restore,
        checkpoint_dir=checkpoint_dir
    )
    env.close()
    return save_path


def get_available_gpus():
    from tensorflow.python.client import device_lib
    local_device_protos = device_lib.list_local_devices()
    return [x.physical_device_desc for x in local_device_protos if x.device_type == 'GPU']


def set_global_seeds(i):
    try:
        import tensorflow as tf
    except ImportError:
        pass
    else:
        tf.set_random_seed(i)
    np.random.seed(i)
    random.seed(i)


def get_session():
    tf.reset_default_graph()
    tf_config = tf.ConfigProto(
        inter_op_parallelism_threads=1,
        intra_op_parallelism_threads=1)
    session = tf.Session(config=tf_config)
    print("AVAILABLE GPUS: ", get_available_gpus())
    return session


def get_env(env_id, seed):
    env = gym.make(env_id)

    set_global_seeds(seed)
    env.seed(seed)

    expt_dir = './tmp/hw3_vid_dir2/'
    env = wrappers.Monitor(env, osp.join(expt_dir, "gym"), force=True)
    env = wrap_deepmind(env)

    return env


def get_custom_env(env_id, seed):
    env = gym.make(env_id)

    set_global_seeds(seed)
    env.seed(seed)

    expt_dir = './tmp/hw3_vid_dir2/'
    env = wrappers.Monitor(env, osp.join(expt_dir, "gym"), force=True)
    env = wrap_custom(env)

    return env


def main():
    arguments = docopt.docopt(__doc__)

    # Run training
    seed = 0  # Use a seed of zero (you may want to randomize the seed!)
    env = get_env(arguments['--envid'], seed)
    with get_session() as session:

        model = arguments['--model'].lower()
        num_filters = int(arguments['--num-filters'])
        batch_size = int(arguments['--batch-size'])
        print(' * [INFO] %s model (Filters: %d, Batch Size: %d)' % (
            model, num_filters, batch_size))

        save_path = atari_learn(
            env,
            session,
            num_timesteps=int(arguments['--timesteps']),
            num_filters=num_filters,
            model=model,
            batch_size=batch_size,
            restore=arguments['--restore'],
            checkpoint_dir=arguments['--ckpt-dir'],
            learning_starts=arguments['--learning-starts'])
        reader = tf.train.NewCheckpointReader(save_path)
        W = reader.get_tensor('q_func/action_value/fully_connected/weights')
        print('Largest entry:', np.linalg.norm(W, ord=np.inf))
        print('Frobenius norm:', np.linalg.norm(W, ord='fro'))

if __name__ == "__main__":
    main()
