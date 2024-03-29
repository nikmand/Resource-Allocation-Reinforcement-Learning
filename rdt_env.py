import gym
from gym import spaces
import numpy as np
import logging.config
from utils.constants import LC_TAG, BE_TAG
from utils.config_constants import *
import time

from utils.functions import form_duration

logging.config.fileConfig('logging.conf')
log = logging.getLogger('simpleExample')

features_min_max_values = {
    'MPKC': (0, 14),
    'MPKI': (0, 8),
    'Misses': (0, 7*1e7),
    'IPC': (0, 2.8),
    'Bandwidth': (0, 3*1e4)
    }


class Rdt(gym.Env):
    metadata = {'render.modes': ['human']}
    UPDATE_INTERVAL = 1000  # in ms, update status of BEs every 1s

    def __init__(self, config, loader, scheduler, pqos_handler):
        self.loader = loader
        self.scheduler = scheduler
        self.pqos_handler = pqos_handler

        self.latency_thr = int(config[LATENCY_thr])
        self.violations = 0
        self.steps = 1
        self.penalty_coef = float(config[PEN_COEF])
        self.feature = config[FEATURE]

        feature_min, feature_max = features_min_max_values[self.feature]
        log.info("Feature {} will be used with limits: {} - {}".format(self.feature, feature_min, feature_max))

        self.action_space = spaces.Discrete(int(config[NUM_WAYS]))
        # latency, mpki_be # used to be 2*1e6, 5*1e7, ways_be # 14 me 30 gia mpc kai be=mcf
        # for gradient boost high in misses raised to 20 from 14
        self.observation_space = spaces.Box(low=np.array([feature_min, 0]),
                                            high=np.array([feature_max, self.action_space.n-1], dtype=np.float32),
                                            dtype=np.float32)

        self.previous_action = -1  # -1 action means all ways available to all groups

        self.update_interval_in_steps = self.UPDATE_INTERVAL // int(self.loader.measurement_interval)

    def _reset_pqos(self):
        self.pqos_handler.reset()
        self.pqos_handler.setup_groups()
        self.pqos_handler.set_association_class()
        self.pqos_handler.print_association_config()
        self.previous_action = -1

    def _stop_pqos(self):
        self.pqos_handler.stop()
        self.pqos_handler.reset()
        self.pqos_handler.finish()

    @staticmethod
    def _normalize(metric, min_val, max_val):
        """ Normalize the observed value between 1 and 0.  """
        if metric > max_val:
            return 1.0
        elif metric < min_val:
            return 0.0
        else:
            return (metric - min_val) / (max_val - min_val)

    def _get_next_state(self, action_be_ways):
        """  """
        # poll metrics so the next poll will contains deltas from this point just after the action
        self.pqos_handler.update()
        start_time = time.time()
        # start the stats record, the recorder will go to sleep and the it 'll send the results
        tail_latency, rps = self.loader.get_stats()  # NOTE this call will block

        self.pqos_handler.update()
        time_interval = time.time() - start_time
        ipc_hp, misses_hp, llc_hp, mbl_hp_ps, mbr_hp_ps, cycles_hp, instructions_hp =\
            self.pqos_handler.get_hp_metrics(time_interval)
        ipc_be, misses_be, llc_be, mbl_be_ps, mbr_be_ps, cycles_be, instructions_be =\
            self.pqos_handler.get_be_metrics(time_interval)

        # bw_socket_wide = mbl_hp_ps + mbl_be_ps
        # bw_lc = mbl_hp_ps + mbr_hp_ps

        if self.feature == 'IPC':
            feature = ipc_be
        elif self.feature == 'Misses':
            # normalization of misses on a specific time unit in order to compare with different action intervals
            # misses_be = misses_be / (int(self.action_interval) // 50)
            feature = misses_be
        elif self.feature == 'MPKC':
            misses_be = misses_be / (cycles_be / 1000.)
            misses_hp = misses_hp / (cycles_hp / 1000.)
            feature = misses_be
        elif self.feature == 'MPKI':
            misses_be = misses_be / (instructions_be / 1000.)
            misses_hp = misses_hp / (instructions_hp / 1000.)
            feature = misses_be
        elif self.feature == 'Bandwidth':
            feature = mbl_be_ps
        else:
            log.info("No such feature: {}".format(self.feature))
            return

        info = {LC_TAG: (ipc_hp, misses_hp, llc_hp, mbl_hp_ps, mbr_hp_ps, tail_latency, rps),
                BE_TAG: (ipc_be, misses_be, llc_be, mbl_be_ps, mbr_be_ps, None, None)}

        state = [feature, action_be_ways]

        # we normalize as well the be_ways, as it is included in the state
        state_normalized = [self._normalize(metric, min_val, max_val) for metric, min_val, max_val in
                            zip(state, self.observation_space.low, self.observation_space.high)]

        return state_normalized, info, tail_latency

    def _reward_func(self, action_be_ways, hp_tail_latency):
        """ Reward function. """

        if hp_tail_latency < self.latency_thr:
            reward = action_be_ways
            # NOTE by shaping the reward function in this way, we are making the assumption that progress of BEs is
            # depended by the LLC ways that are allocated to them at any point of their execution.
        else:
            reward = - self.penalty_coef * self.action_space.n
            self.violations += 1

        return reward

    def reset(self):
        """ In case that this environment is used in episodic format. """

        self._reset_pqos()
        self.loader.reset()
        self.scheduler.reset()

        state, _, _ = self._get_next_state(self.action_space.n)  # we start with both groups sharing all ways

        log.info("Environment was successfully reset.")

        return state

    def step(self, action_be_ways):
        """ At each step the agent specifies the number of ways that are assigned to the be"""

        # log.debug("Action selected: {}".format(action_be_ways))
        # self.new_be = False

        done = False  # update the status of BEs once in a while to reduce docker demon cpu utilization
        if self.steps % self.update_interval_in_steps == 0:
            done = self.scheduler.update_status()

        # err_msg = "%r (%s) invalid" % (action_be_ways, type(action_be_ways))
        # assert self.action_space.contains(action_be_ways), err_msg

        # avoid enforcing decision when nothing changes. Does this cause any inconsistencies ?
        if action_be_ways != self.previous_action:
            # enforce the decision with PQOS
            self.pqos_handler.set_allocation_class(action_be_ways)
            # self.pqos_handler.print_allocation_config()
            self.previous_action = action_be_ways

        state, info, tail_latency = self._get_next_state(action_be_ways)

        reward = self._reward_func(action_be_ways, tail_latency)  # based on new metrics

        self.steps += 1

        return state, reward, done, info  # , self.new_be

    def render(self, **kwargs):
        pass

    def get_experiment_duration(self):
        """ Properly shapes and returns the time needed for the experiment to finish. """

        return self.scheduler.get_experiment_duration()

    def stop(self):
        log.warning('Stopping everything!')

        duration = form_duration(self.get_experiment_duration())

        log.info('Percentage of violations: {}'.format(self.violations / self.steps))
        log.info('Duration of experiment: {}'.format(duration))

        self.scheduler.stop_bes()  # stop and remove the be containers
        self.loader.stop()  # stop the service loader
        self._stop_pqos()  # stop pqos
