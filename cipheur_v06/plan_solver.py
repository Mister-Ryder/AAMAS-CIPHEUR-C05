"""Dual-timescale finite structural plans with whole-interval delayed credit.

All selectors see the same finite plans. The main thread alone owns native
state; the HTTP thread sees a detached observation and never owns the engine.
This module is an exploratory candidate until the frozen study passes.
"""
import copy,time
from cipheur_v04.graph import load_graph
from cipheur_v04.program import default_program
from cipheur_v05.native import Engine
from cipheur_v05.schema import Config as NativeConfig
from cipheur_v05.provider import SlowLane
from .contracts import Config
from .actions import execute,BASELINE_ACTION
from .context import Observer
from .solver import LIBRARY
from .plan_context import augment_packet
from .plans import build_plans,PlanController
from .plan_contracts import resolve_choice,plan_identity
from .directed_actions import execute_directed
from .compact_plans import compact_packet, expand_plan_library

MODEL_MODES=('llm','serial','shadow','no_history','no_resource')
CONTROL_MODES=()  # Classical selectors are released on the controls branch.

def solve(path,mode='llm',config=None,provider=None,on_event=None,graph_id=None):
    cfg=config or Config();begin=time.perf_counter();cpu_begin=time.process_time();deadline=begin+cfg.seconds
    if mode not in MODEL_MODES+CONTROL_MODES:raise ValueError('Unknown structural-plan mode')
    model=mode in MODEL_MODES
    if model and provider is None:raise ValueError('Real provider or explicit fixture required')
    g=load_graph(path);initial=g.degree_seed();initial_value=g.weight(initial)
    observer=Observer(g);observer.history_limit=cfg.context_history
    native_cfg=NativeConfig(seconds=cfg.seconds,seed=cfg.seed,population=4,epoch_seconds=cfg.epoch_seconds,
        max_epochs=1000000,max_cycles_per_epoch=100000000,events=0,max_calls=0,
        initialization_work=10000000,local_seconds=.1)
    controller=PlanController(mode if mode in CONTROL_MODES and mode!='adaptive' else 'rule',cfg.seed)
    lane=SlowLane(provider) if model and mode!='serial' else None
    history=[];calls=[];installs=[];decisions=[];ledger=[];transitions=[]
    active=None;active_origin='hand_adaptive';active_install=None;active_packet=None
    macro_start=0.;macro_value=initial_value;macro_pool=0;macro_epoch_start=0
    step_index=0;step_start=0.;step_enter=True
    next_request=cfg.first_decision;request_number=0
    pending=None;next_classical=cfg.first_decision;classical_number=0

    def event(obj):
        if on_event:on_event(copy.deepcopy(obj))

    def observe(engine):
        elapsed=time.perf_counter()-begin;snapshot=engine.snapshot()
        obs=observer.packet(snapshot,history,elapsed,cfg.seconds,LIBRARY)
        obs=augment_packet(obs,snapshot,g)
        obs['plan_outcomes']=copy.deepcopy(ledger[-8:])
        if active is not None and active_packet is not None:
            obs['current_plan']={'plan_id':active['plan_id'],'operation':copy.deepcopy(active.get('operation')),
                'origin':active_origin,'elapsed_seconds':max(0.,elapsed-macro_start),
                'observed_gain_ticks':snapshot['value_ticks']-macro_value,
                'observed_pool_gain_ticks':sum(s['value_ticks'] for s in snapshot['slots'])-macro_pool,
                'step_index':step_index,'right_censored':True}
        registry=build_plans(obs)
        obs['plan_library']={key:{'steps':copy.deepcopy(d['steps']),'operation':copy.deepcopy(d.get('operation')),
            'identity':plan_identity(d)} for key,d in registry.items()}
        obs['plan_space_scope']='All selectors share the same frozen whole-plan registry. Directed plan effects are unmeasured until executed; no short oracle labels are supplied.'
        return obs,registry

    def close_macro(engine,reason):
        nonlocal active_packet
        if active is None or active_packet is None:return
        snap=engine.snapshot(False);elapsed=time.perf_counter()-begin;seconds=max(0.,elapsed-macro_start)
        gain=snap['value_ticks']-macro_value
        performed=sorted({h['step_index'] for h in history[macro_epoch_start:] if h['status']=='ok'})
        record={'index':len(ledger),'plan_id':active['plan_id'],'descriptor':copy.deepcopy(active),
            'origin':active_origin,'install_index':active_install,'start_elapsed_seconds':macro_start,
            'elapsed_seconds':elapsed,'wall_seconds':seconds,'gain_ticks':gain,
            'pool_gain_ticks':sum(s['value_ticks'] for s in snap['slots'])-macro_pool,
            'transition':reason,'planned_steps':len(active['steps']),
            'last_observed_step_index':step_index,'observed_step_indices':performed,
            'partial_plan':any(i not in performed for i in range(len(active['steps']))),
            'bound_snapshot_id':active_packet['snapshot_id']}
        if active_origin!='hand_adaptive' and seconds>0:
            record['learning_receipt']=controller.update(active_packet,active,gain,seconds)
        else:
            record['learning_scope']='The hand-adaptive fallback interval is retained as history, not a warm reward label for later plans.'
        ledger.append(record);event({'event':'plan_interval_closed',**record})
        active_packet=None

    def install(descriptor,obs,origin,choice=None,call_index=None):
        nonlocal active,active_packet,active_origin,active_install,macro_start,macro_value,macro_pool,macro_epoch_start
        nonlocal step_index,step_start,step_enter
        close_macro(engine,'replaced')
        active=copy.deepcopy(descriptor);active_packet=copy.deepcopy(obs);active_origin=origin
        active_install=len(installs) if call_index is not None else None
        snap=engine.snapshot(False);macro_start=time.perf_counter()-begin;macro_value=snap['value_ticks']
        macro_pool=sum(s['value_ticks'] for s in snap['slots'])
        macro_epoch_start=len(history)
        step_index=0;step_start=time.perf_counter();step_enter=True
        decision={'elapsed_seconds':macro_start,'plan_id':active['plan_id'],'descriptor':copy.deepcopy(active),
            'origin':origin,'bound_snapshot_id':obs['snapshot_id'],'choice':copy.deepcopy(choice)}
        decisions.append(decision)
        if call_index is not None:
            row={'call_index':call_index,'elapsed_seconds':macro_start,'choice':copy.deepcopy(choice),
                'contract':copy.deepcopy(active),'contract_sha256':plan_identity(active),'origin':origin}
            installs.append(row);event({'event':'contract_installed',**row})

    def accept(result):
        nonlocal pending
        sub=result['submission'];meta=sub['metadata'];receipt=result['receipt']
        call={'index':meta['call_index'],'request_elapsed':meta['elapsed_seconds'],
            'received_elapsed':time.perf_counter()-begin,'prompt':sub['payload'],'receipt':receipt,
            'raw_response':result['raw_response'],'response':result['response'],'disposition':'rejected'}
        if 'transport_started_monotonic' in receipt and 'transport_finished_monotonic' in receipt:
            call['http_request_elapsed']=receipt['transport_started_monotonic']-begin
            call['http_return_elapsed']=receipt['transport_finished_monotonic']-begin
        try:
            if pending is None or result['response'] is None:raise ValueError('No valid response')
            if time.perf_counter()-sub['submitted']>cfg.response_ttl:raise ValueError('Response TTL exceeded')
            obs,registry=pending
            bound_packet=copy.deepcopy(sub['payload']['observation'])
            bound_packet['plan_library']=expand_plan_library(bound_packet['plan_library'])
            choice,descriptor=resolve_choice(result['response'],bound_packet,registry)
            if time.perf_counter()>=deadline:raise ValueError('Solver deadline reached')
            call['contract_sha256']=plan_identity(descriptor)
            call['resolved_plan']=copy.deepcopy(descriptor)
            call['native_cycles_at_receive']=engine.snapshot(False)['cycles']
            call['native_cycles_while_pending']=call['native_cycles_at_receive']-meta['native_cycles']
            if mode=='shadow':call['disposition']='validated_shadow'
            else:
                install(descriptor,obs,'llm_http' if receipt.get('model_calls') else receipt.get('origin','fixture'),choice,call['index'])
                call['disposition']='installed'
        except Exception as exc:call['error']=type(exc).__name__+': '+str(exc)
        calls.append(call);event({'event':'call_received','index':call['index'],
            'disposition':call['disposition'],'receipt':receipt});pending=None

    def model_packet(obs):
        packet=copy.deepcopy(obs)
        if mode=='no_history':
            for key in ('history','history_summary','action_outcomes','plan_outcomes','prior_counterfactual_examples'):
                packet.pop(key,None)
            if 'current_plan' in packet:
                packet['current_plan']={k:v for k,v in packet['current_plan'].items() if k in ('plan_id','operation','step_index')}
        if mode=='no_resource':
            packet.pop('resources',None);packet.pop('repair_slot_scope',None)
            packet['static']={k:v for k,v in packet['static'].items() if k in ('n','m','mean_degree','objective')}
            packet['repair_slots']=[{k:v for k,v in s.items() if k in ('slot','value_ticks','value_gap_ticks')} for s in packet.get('repair_slots',[])]
            for outcome in packet.get('plan_outcomes',[]):outcome['descriptor'].pop('context',None)
        return compact_packet(packet)

    with Engine(g,initial,4,cfg.seed,default_program()) as engine:
        obs,registry=observe(engine)
        install(registry[BASELINE_ACTION],obs,'hand_adaptive')
        while time.perf_counter()<deadline:
            if lane:
                result=lane.poll()
                if result is not None:accept(result)
            elapsed=time.perf_counter()-begin
            if model and elapsed>=next_request and request_number<cfg.max_calls and deadline-time.perf_counter()>cfg.request_timeout+2 and not (lane and lane.busy):
                obs,registry=observe(engine);pending=(obs,registry)
                payload={'protocol':'cipheur_v060_structural_plan_compact','observation':model_packet(obs),
                    'previous_decisions':copy.deepcopy(decisions[-8:]) if mode!='no_history' else [],
                    'goal':'Compare the opportunity cost of continuing the current control with alternative native plans. Choose the strongest supported whole plan for remaining incumbent improvement; positive cumulative gain alone does not establish that the current control is best.'}
                if mode=='no_resource':
                    for decision in payload['previous_decisions']:decision['descriptor'].pop('context',None)
                request_number+=1
                metadata={'call_index':request_number,'elapsed_seconds':time.perf_counter()-begin,'native_cycles':engine.snapshot(False)['cycles']}
                next_request=cfg.first_decision+request_number*cfg.decision_interval
                event({'event':'call_submitted',**metadata,'snapshot_id':obs['snapshot_id']})
                if mode=='serial':
                    submitted=time.perf_counter();result=provider.complete(payload,min(cfg.request_timeout,max(.01,deadline-submitted)))
                    result['submission']={'payload':payload,'metadata':metadata,'submitted':submitted};accept(result)
                else:lane.start(payload,min(cfg.request_timeout,max(.01,deadline-time.perf_counter())),metadata)
            if time.perf_counter()>=deadline:break
            elapsed=time.perf_counter()-begin
            if not model and mode!='adaptive' and elapsed>=next_classical and classical_number<cfg.max_calls:
                close_macro(engine,'decision_checkpoint')
                obs,registry=observe(engine);plan_id=controller.choose(obs,registry)
                if plan_id not in registry:raise ValueError('Classical selector proposed unavailable plan')
                choice={'snapshot_id':obs['snapshot_id'],'plan_id':plan_id,'hypothesis':'shared finite-plan '+mode,'evidence':[]}
                install(registry[plan_id],obs,'classical_'+mode,choice)
                classical_number+=1;next_classical=cfg.first_decision+classical_number*cfg.decision_interval
            step=active['steps'][step_index]
            step_remaining=step['seconds']-(time.perf_counter()-step_start)
            if step_remaining<=0:
                transitions.append({'plan_id':active['plan_id'],'install_index':active_install,'step_index':step_index,
                    'elapsed_seconds':time.perf_counter()-begin,'cause':'planned_dwell_complete','entered':not step_enter})
                if step_index+1<len(active['steps']):step_index+=1;step_enter=True
                else:step_enter=False
                step_start=time.perf_counter();continue
            allocated=min(cfg.epoch_seconds,max(0.,deadline-time.perf_counter()),step_remaining)
            start=time.perf_counter();was_enter=step_enter
            if step['action']=='directed_reseed':
                effect=execute_directed(engine,active['operation'],native_cfg,allocated,enter=step_enter)
            else:effect=execute(engine,step['action'],native_cfg,allocated,enter=step_enter)
            end=time.perf_counter();step_enter=False
            item={'index':len(history),'action':step['action'],'plan_id':active['plan_id'],'origin':active_origin,
                'install_index':active_install,'step_index':step_index,'entry':was_enter,
                'elapsed_seconds':end-begin,'start_elapsed_seconds':start-begin,'wall_seconds':effect['wall_seconds'],
                'gain_ticks':effect['gain_ticks'],'pool_gain_ticks':effect['pool_gain_ticks'],'cycles':effect['cycles'],
                'value_ticks':effect['after']['value_ticks'],'diversity_ppm':effect['after']['diversity_ppm'],
                'before_best_slot':effect['before']['best_slot'],
                'diversity_before_ppm':effect['before']['diversity_ppm'],'status':effect['status'],
                'interventions':[e for e in effect['effects'] if e['type'] in ('kick','reseed','directed_reseed')],
                'model_call_pending':bool(lane and lane.busy),'program_sha256':effect['program_sha256']}
            history.append(item)
            if step['action']=='directed_reseed':
                item['directed_execution']={key:copy.deepcopy(effect.get(key)) for key in (
                    'operation','requested_root','requested_slot','actual_root','actual_slot','seed_installed',
                    'seed_boundary','priority_sha256','priority_ast','reason','continuation_scope')}
                item['directed_execution']['protected_best_slot']=effect['before']['best_slot']
            if effect['status']!='ok':
                event({'event':'native_action_rejected','plan_id':active['plan_id'],'action':step['action'],'status':effect['status']})
                transitions.append({'plan_id':active['plan_id'],'install_index':active_install,'step_index':step_index,
                    'elapsed_seconds':end-begin,'cause':effect['status'],'entered':was_enter})
                if step_index+1<len(active['steps']):step_index+=1;step_enter=True;step_start=end
                else:
                    obs,registry=observe(engine);install(registry[BASELINE_ACTION],obs,'native_validation_fallback')
        if lane:
            late=lane.cutoff()
            if late:
                sub=late['submission'];calls.append({'index':sub['metadata']['call_index'],
                    'request_elapsed':sub['metadata']['elapsed_seconds'],'received_elapsed':time.perf_counter()-begin,
                    'prompt':sub['payload'],'receipt':late['receipt'],'raw_response':late['raw_response'],
                    'response':late['response'],'disposition':'deadline_discarded'})
        close_macro(engine,'solver_deadline');result=engine.report();native_hash=engine.binary_sha256
    selected=set(result['selected'])
    if not g.feasible(selected) or g.weight(selected)!=result['value_ticks'] or result['value_ticks']<initial_value:
        raise AssertionError('Original integer objective/graph feasibility failed')
    overlap=0.;http_overlap=0.
    for call in calls:
        lo=call['request_elapsed'];hi=call['received_elapsed']
        overlap+=sum(max(0.,min(hi,h['elapsed_seconds'])-max(lo,h['start_elapsed_seconds'])) for h in history)
        if 'http_request_elapsed' in call:
            lo=call['http_request_elapsed'];hi=call['http_return_elapsed']
            http_overlap+=sum(max(0.,min(hi,h['elapsed_seconds'])-max(lo,h['start_elapsed_seconds'])) for h in history)
    usage={'known_prompt_tokens':0,'known_completion_tokens':0,'calls_with_missing_usage':0}
    for call in calls:
        u=call['receipt'].get('usage')
        if isinstance(u,dict) and type(u.get('prompt_tokens')) is int and type(u.get('completion_tokens')) is int:
            usage['known_prompt_tokens']+=u['prompt_tokens'];usage['known_completion_tokens']+=u['completion_tokens']
        elif call['receipt'].get('model_calls'):usage['calls_with_missing_usage']+=1
    wall=time.perf_counter()-begin
    result.update(schema=6,release_status='development_candidate',algorithm='CIPHEUR-structural-temporal-plan',
        control_protocol='shared_structural_whole_plan',mode=mode,status='ok',feasible=True,config=cfg.json(),
        seed=cfg.seed,n=g.n,m=g.m,graph_path=str(path),graph_id=graph_id,input_sha256=g.source_sha256,
        native_sha256=native_hash,initial_value_ticks=initial_value,total_weight_ticks=sum(g.weights),
        target_seconds=cfg.seconds,wall_seconds=wall,cpu_seconds=time.process_time()-cpu_begin,
        overrun_seconds=max(0.,wall-cfg.seconds),
        deadline_scope='soft end-to-end graph load, observation, native work, explicit repair, HTTP overlap/waits and integer verification; JSON persistence excluded',
        epochs=history,model_calls=calls,installed_contracts=installs,classical_decisions=decisions,
        plan_intervals=ledger,plan_step_transitions=transitions,model_usage=usage,
        deployment_llm_calls=sum(c['receipt'].get('model_calls',0) for c in calls),
        valid_model_proposals=sum(c['disposition'] in ('installed','validated_shadow') for c in calls),
        native_wall_overlapping_model_wait_seconds=overlap,native_action_wall_overlapping_http_seconds=http_overlap,
        native_cycles_while_model_pending=sum(h['cycles'] for h in history if h['model_call_pending']),
        action_library=LIBRARY,note='Model selects predeclared data-only native plans. Delayed interval credit is observational; no advantage is assumed.')
    return result
