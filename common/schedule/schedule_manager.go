/*
 * Copyright (c) 2019 Huawei Technologies Co., Ltd.
 * A-Tune is licensed under the Mulan PSL v1.
 * You can use this software according to the terms and conditions of the Mulan PSL v1.
 * You may obtain a copy of Mulan PSL v1 at:
 *     http://license.coscl.org.cn/MulanPSL
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY OR FIT FOR A PARTICULAR
 * PURPOSE.
 * See the Mulan PSL v1 for more details.
 * Create: 2020-03-05
 */

package schedule

import (
	PB "atune/api/profile"
	"atune/common/log"
	"atune/common/schedule/framework"
	"atune/common/schedule/plugins"
	"atune/common/sysload"
	"atune/common/topology"
	"atune/common/utils"
	"atune/common/sched"
	"context"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"
)

const period = 10

var bindTaskMap = make(map[uint64]*framework.BindTaskInfo)

var once sync.Once
var scheduleManager *ScheduleManager

type scheduleOption struct {
	save bool
	ch   chan *PB.AckCheck
}

// Option: func type to set the function options
type Option func(*scheduleOption)

// WithSave return the Option with save
func WithSave(save bool) Option {
	return func(s *scheduleOption) {
		s.save = save
	}
}

// WithChannel return the Option with channel ch
func WithChannel(ch chan *PB.AckCheck) Option {
	return func(s *scheduleOption) {
		s.ch = ch
	}
}

var defaultOptions = scheduleOption{
	save: false,
}

// ScheduleManager: manage the general scheduler service
type ScheduleManager struct {
	sync.Mutex
	running   bool
	plugins   map[string]framework.Plugin
	scheduler *generalScheduler
}

// Init scheduleManager instance
func (s *ScheduleManager) Init() error {
	topology.InitTopo()
	s.plugins = make(map[string]framework.Plugin)

	for name, factory := range plugins.PluginTables() {
		p, err := factory()
		if err != nil {
			return fmt.Errorf("init plugin %s fail:%v", name, err)
		}
		s.plugins[name] = p
	}

	return nil
}

// New method return the general scheduler instance
func (s *ScheduleManager) New(strategy string, pids string, opts ...Option) (*generalScheduler, error) {
	options := defaultOptions

	for _, opt := range opts {
		opt(&options)
	}

	ctx, cancle := context.WithCancel(context.Background())
	plugin, ok := s.plugins[strategy]
	if !ok {
		return nil, fmt.Errorf("strategy not support : %s", strategy)
	}
	scheduler := &generalScheduler{
		strategy: strategy,
		pids:     pids,
		ctx:      ctx,
		stop:     cancle,
		ch:       options.ch,
		plugin:   plugin,
	}

	return scheduler, nil
}

func (s *ScheduleManager) Start() {
	if s.IsRunning() {
		return
	}

	if s.isSchdulerNill() {
		return
	}

	s.setRunning(true)
	go func() {
		s.scheduler.Run()
	}()
	<-s.scheduler.Context().Done()
	s.setRunning(false)
}

func (s *ScheduleManager) setRunning(running bool) {
	s.Lock()
	defer s.Unlock()

	s.running = running
}

func (s *ScheduleManager) IsRunning() bool {
	s.Lock()
	defer s.Unlock()

	return s.running
}

func (s *ScheduleManager) isSchdulerNill() bool {
	s.Lock()
	defer s.Unlock()

	return s.scheduler == nil
}

// Submit method add the scheduler to scheduler manager
func (s *ScheduleManager) Submit(sched *generalScheduler) {
	s.Lock()
	defer s.Unlock()

	if s.running == false {
		s.scheduler = sched
		return
	}

	if s.scheduler == nil {
		s.scheduler = sched
		return
	}

	if s.scheduler.Name() == sched.Name() {
		log.Infof("the scheduler %s has been in running", s.scheduler.Name())
		return
	}

	s.scheduler.Stop()
	s.scheduler = sched

}

// Stop method stop the general schesuler which is in running
func (s *ScheduleManager) Stop() {
	s.Lock()
	defer s.Unlock()
	if s.running == false {
		return
	}

	if s.scheduler == nil {
		return
	}

	s.scheduler.Stop()
	s.running = false
	log.Info("stop the scheduler %s success", s.scheduler.Name())
	s.scheduler = nil
}

// GetScheduleManager return the schedule manager instance
func GetScheduleManager() *ScheduleManager {
	once.Do(func() {
		scheduleManager = &ScheduleManager{}
		scheduleManager.Init()
	})
	return scheduleManager
}

type generalScheduler struct {
	strategy string
	pids     string
	ctx      context.Context
	stop     context.CancelFunc
	plugin   framework.Plugin
	ch       chan *PB.AckCheck
}

// Run method start the general scheduler service
func (g *generalScheduler) Run() error {
	var taskinfo []*framework.BindTaskInfo

	for _, pidstr := range strings.Split(g.pids, ",") {
		if strings.Contains(pidstr, "-") {
			pidRange := strings.Split(pidstr, "-")
			minPid, _ := strconv.ParseUint(pidRange[0], utils.DecimalBase, utils.Uint64Bits)
			maxPid, _ := strconv.ParseUint(pidRange[1], utils.DecimalBase, utils.Uint64Bits)
			for pid := minPid; pid <= maxPid; pid++ {
				var ti framework.BindTaskInfo
				ti.Pid = pid
				ti.Node = topology.DefaultSelectNode()
				taskinfo = append(taskinfo, &ti)
			}
			continue
		}
		var ti framework.BindTaskInfo
		pid, err := strconv.ParseUint(pidstr, utils.DecimalBase, utils.Uint64Bits)
		if err != nil {
			return fmt.Errorf("pids error : %s", err)
		}
		ti.Pid = pid
		ti.Node = topology.DefaultSelectNode()
		taskinfo = append(taskinfo, &ti)
	}

	ticker := time.NewTicker(time.Second * period)

	g.plugin.Preprocessing(&taskinfo)
	sendChanToAdm(g.ch, "Preprocessing", utils.SUCCESS, "")
	bindFlag := false
	for {
		select {
		case <-g.ctx.Done():
			return g.ctx.Err()
		case <-ticker.C:
			sysload.Sysload.Update()
			updateNodeLoad()

			if !bindFlag {
				g.plugin.PickNodesforPids(&taskinfo)
				sendChanToAdm(g.ch, "PickNodesforPids Results:", utils.SUCCESS, "")

				for _, ti := range taskinfo {
					sendChanToAdm(g.ch, strconv.FormatUint(ti.Pid, utils.DecimalBase), utils.INFO, fmt.Sprintf(
						"type:%v id:%v", ti.Node.Type(), ti.Node.ID()))
				}

				bindTaskToTopoNode(&taskinfo)
				bindFlag = true

				for _, task := range taskinfo {
					sysload.Sysload.AddTask(task.Pid)
				}
				sendChanToAdm(g.ch, "bindTaskToTopoNode finished", utils.SUCCESS, "")
			}

			g.plugin.Postprocessing(&taskinfo)
			sendChanToAdm(g.ch, "Postprocessing", utils.SUCCESS, "")
		}
	}
	return nil

}

// Stop the general scheduler service
func (g *generalScheduler) Stop() {
	g.stop()
}

// Name return the general scheduler name
func (g *generalScheduler) Name() string {
	return g.strategy
}

// Context return the general scheduler ctx
func (g *generalScheduler) Context() context.Context {
	return g.ctx
}

func setTaskAffinity(tid uint64, node *topology.TopoNode) error {
	var cpus []uint32

	for cpu := node.Mask().Foreach(-1); cpu != -1; cpu = node.Mask().Foreach(cpu) {
		cpus = append(cpus, uint32(cpu))
	}
	log.Info("bind ", tid, " to cpu ", cpus)
	if err := sched.SetAffinity(uint64(tid), cpus); err != nil {
		return err
	}
	return nil
}

func bindTaskToTopoNode(taskInfo *[]*framework.BindTaskInfo) {
	for _, ti := range *taskInfo {
		if isThreadBind(ti.Pid) {
			continue
		}
		if err := setTaskAffinity(ti.Pid, ti.Node); err != nil {
			log.Error(err)
		}
		bindTaskMap[ti.Pid] = ti
		ti.Node.AddBind()
	}
}

func isThreadBind(tid uint64) bool {
	_, ok := bindTaskMap[tid]
	return ok
}

type updateLoadCallback struct {
	load *sysload.SystemLoad
}

// callback to update cpu load
func (callback *updateLoadCallback) Callback(node *topology.TopoNode) {
	cpu := node.ID()
	load := callback.load.GetCPULoad(cpu)
	node.SetLoad(load)
}

func updateNodeLoad() {
	var callback updateLoadCallback
	callback.load = &sysload.Sysload
	topology.ForeachTypeCall(topology.TopoTypeCPU, &callback)
}
