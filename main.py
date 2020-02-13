import gym
import constants
import algorithms
import numpy as np
import logging.config
from quantization import Quantization
from q_agent import QAgent
from sarsa_agent import SARSAgent


def train_loop():  # consider the possible to create a class trainer
    """"""
    for i_episode in range(constants.train_episodes):
        observation = env.reset()  #
        agent.adjust_exploration(i_episode)
        agent.adjust_lr(i_episode)

        state = quantizator.digitize(observation)
        action = agent.choose_action(state)

        # logger.debug(state)
        # logger.debug(action)

        for step in range(constants.max_steps):
            env.render()
            observation, reward, done, info = env.step(action)  # takes the specified action
            if done:
                print("Episode {} terminated after {} timesteps".format(i_episode, step + 1))
                break

            new_state = quantizator.digitize(observation)
            new_action = agent.choose_action(new_state)

            # logger.debug(new_state)
            # logger.debug(new_action)

            agent.update(state, action, reward, new_state, new_action)  # if q-learning new action is not going to be used

            state = new_state
            action = new_action
        else:
            print("Episode {} finished successful!".format(i_episode))


def eval_loop():
    for i_episode in range(constants.eval_episodes):
        observation = env.reset()  #

        state = quantizator.digitize(observation)
        action = agent.choose_action(state, train=False)

        for step in range(constants.max_steps):
            env.render()
            observation, reward, done, info = env.step(action)  # takes the specified action
            if done:
                print("Episode {} terminated after {} timesteps".format(i_episode, step + 1))
                break

            state = quantizator.digitize(observation)
            action = agent.choose_action(state, train=False)
        else:
            print("Episode {} finished successful!".format(i_episode))


if __name__ == "__main__":
    """The problem is considered solved when the poll stays upright for over 195 time steps, 100 times consecutively"""

    logging.config.fileConfig('logging.conf')
    logger = logging.getLogger('simpleExample')
    env = gym.make(constants.environment)
    high_intervals = env.observation_space.high
    low_intervals = env.observation_space.low

    logger.debug(high_intervals)
    logger.debug(low_intervals)

    vars_ls = list(zip(low_intervals, high_intervals, constants.var_freq))
    quantizator = Quantization(vars_ls, lambda x: [x[i] for i in [2, 3]])

    logger.debug(quantizator.vars_bins)

    algorithm = algorithms.QLEARNING

    if algorithm == algorithms.QLEARNING:
        agent = QAgent(env, quantizator.dimensions)
    elif algorithm == algorithms.SARSA:
        agent = SARSAgent(env, quantizator.dimensions)
    else:
        raise NotImplementedError

    logger.debug(quantizator.dimensions)
    logger.debug(agent.q_table.shape)

    train_loop()

    eval_loop()

    env.close()
