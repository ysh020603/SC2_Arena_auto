from .base_player import BasePlayer
from sc2.unit import Unit
from sc2.units import Units
from agents import PlanAgent, ActionAgent, RagAgent, SingleAgent, AdjestAgent
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.buff_id import BuffId
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2
from typing import Dict, Any, Set, List

import random
import random
import math


class LLMPlayer(BasePlayer):
    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        
        agent_config = {
            "model_name": self.model_name,
            "generation_config": self.generation_config,
            "llm_client": self.llm_client,
        }

        if config.enable_rag:
            self.rag_agent = RagAgent(config.own_race, **agent_config)
        if config.enable_plan or config.enable_plan_verifier:
            self.plan_agent = PlanAgent(config.own_race, **agent_config)
            self.action_agent = ActionAgent(config.own_race, **agent_config)
            # [!! 在这里添加 !!]
            # 默认初始化 AdjestAgent，它将使用相同的 agent_config
            self.adjest_agent = AdjestAgent(log_dir=self.log_path, **agent_config)
        else:
            self.agent = SingleAgent(config.own_race, **agent_config)

        self.plan_verifier = "llm" if config.enable_plan_verifier else None
        self.action_verifier = self.verify_actions if self.config.enable_action_verifier else None

        self.next_decision_time = -1
        
        # SCV auto-attack settings
        self.scv_auto_attack_distance = 4
        self.scv_auto_attack_time = 240

        # 用于自动防御 记录敌我 任务指派关系
        self.active_defense_map = {}
        
        # 侦察逻辑变量
        self.active_scout_unit_tag = None  # 当前正在执行侦察任务的单位的tag
        self.scouting_information = {}      # 存储侦察信息的字典 {time: [info_string, ...]}
        self.scouted_locations = set()      # 已经侦察过的目标地点 (Point2)
        self.scv_scout_sent = False         # 确保只在游戏初期派遣一次 SCV
        self.scout_target_location = None   # 当前侦察兵的目标地点
        self.known_enemy_tags_in_vision = set() # 追踪当前在视野中的敌人, 以便检测新敌人
        
        self.structure_rally_points = {} # 记录每个生产建筑的集结点 {structure_tag: target_point}

        # 驻防逻辑变量
        self.GARRISON_PERPENDICULAR_DISTANCE = 15  # 驻防点P3/P4：与基地连线的垂直距离
        self.GARRISON_EXTENSION_DISTANCE = 10      # 驻防点P2：从主集结点P1向基地延伸的距离
        self.GARRISON_CHECK_RADIUS = 4            # 驻防点检查半径：判断单位是否“在点上”的范围
        self.GARRISON_DEFENSE_ZONE_RADIUS = 30    # 驻防防区半径：从此半径内调配空闲单位
        self.last_garrison_check_time = -1        # 上次执行驻防逻辑的游戏帧数（iteration）

        # 【总攻系统】用于追踪所有已发起的“总攻”编队
        # 结构: { wave_id: {"unit_tags": {tag1, tag2, ...}, "target_tag": int} }
        self.total_attack_groups: Dict[int, Dict[str, Any]] = {}
        # 用于生成唯一总攻ID的计数器
        self.total_attack_wave_id_counter: int = 0

        # 定义敌方基地建筑类型
        self.enemy_townhall_types = {
            UnitTypeId.COMMANDCENTER,
            UnitTypeId.ORBITALCOMMAND,
            UnitTypeId.PLANETARYFORTRESS,
            UnitTypeId.NEXUS,
            UnitTypeId.HATCHERY,
            UnitTypeId.LAIR,
            UnitTypeId.HIVE,
        }

        self.flag_test = True

    async def distribute_workers(self, resource_ratio: float = 2.0) -> None:
        """
        根据全局矿气比分配工人，优先将工人派往采集gas。
        会将gas_site附近采集mineral的worker调往gas_site以最大化其利用。
        """
        active_workers = self.workers.filter(lambda w: w.tag != self.active_scout_unit_tag)

        if not self.townhalls.ready or not active_workers: # <-- 2. [修改] 检查新列表
            return

        # 1. 收集所有基地周围的矿点和气矿
        mineral_patches = {
            m for nexus in self.townhalls.ready
            for m in self.mineral_field.closer_than(12, nexus)
        }
        gas_refineries = {
            g for nexus in self.townhalls.ready
            for g in self.gas_buildings.ready.closer_than(12, nexus)
            if g.has_vespene
        }

        # 2. 派遣MULE（如果是人族）
        if self.config.own_race == "Terran":
            await self._deploy_mules(mineral_patches)



# 3. 处理gas_site超员问题，将多余的worker释放出来加入可用工人池
        available_idle_workers = list(active_workers.idle)
        
        # [修改] 遍历所有气矿 (gas_refineries)，而不是所有工人
        for gas_site in gas_refineries:
            if gas_site.surplus_harvesters > 0:
                # 找到正在采集这个gas_site的工人
                gas_workers = []
                # [修改] 使用 'w' 作为内部循环变量名, 避免混淆
                for w in self.workers.gathering: 
                    # 通过距离判断worker是否在采集这个gas_site
                    if w.distance_to(gas_site) < 2:
                        gas_workers.append(w)
                
                # 计算需要释放的工人数量
                excess_count = gas_site.surplus_harvesters
                
                # 将多余的工人加入到可用工人池中（取前excess_count个）
                for i in range(min(excess_count, len(gas_workers))):
                    worker_to_reassign = gas_workers[i] # [修改] 使用新变量名
                    available_idle_workers.append(worker_to_reassign)
                    print(f"Marked excess worker from gas for reassignment: {gas_site}")

        # 4. 统计每个点的缺工数
        gas_tasks = {}
        mineral_tasks = {}

        # 气矿：ideal=3，surplus_harvesters<0 时表示缺工
        for g in gas_refineries:
            missing = max(0, -g.surplus_harvesters)
            if missing:
                gas_tasks[g] = missing

        # 矿点：每个矿最多2个工人（不计算MULE和即将被重新分配的工人）
        for m in mineral_patches:
            # 统计该矿点的工人数量（不包括MULE和即将被重新分配的工人）
            worker_count = 0
            for worker in self.workers.gathering:
                if (worker.distance_to(m) < 2 and 
                    worker not in available_idle_workers):
                    worker_count += 1
            need = max(0, 2 - worker_count)
            if need:
                mineral_tasks[m] = need

        # 5. 优先处理gas_sites - 从附近mineral_sites调worker + idle_workers
        for gas_site in list(gas_tasks.keys()):
            needed = gas_tasks[gas_site]
            if needed <= 0:
                continue
            
            # 找到这个gas_site附近正在采集mineral的workers（排除即将被重新分配的工人）
            nearby_mineral_workers = []
        
            for mineral in mineral_patches:
                # 只考虑距离gas_site较近的mineral
                if mineral.distance_to(gas_site) < 10:  # 距离阈值可调整
                    # 找到正在采集这个mineral的workers
                    for worker in self.workers.gathering:
                        # 通过距离判断worker是否在采集这个mineral，且不在重新分配列表中
                        if (worker.distance_to(mineral) < 2 and 
                            worker not in available_idle_workers):
                            nearby_mineral_workers.append((worker, worker.distance_to(gas_site)))
        
            # 按距离gas_site的远近排序，优先调用最近的workers
            nearby_mineral_workers.sort(key=lambda x: x[1])
        
            # 重新分配mineral workers到gas_site
            reassigned = 0
            for worker, _ in nearby_mineral_workers:
                if reassigned >= needed:
                    break
                worker.gather(gas_site)
                print(f"Reassigned worker from mineral to gas: {gas_site}")
                reassigned += 1
        
            # 更新gas_site的需求
            needed -= reassigned
        
            # 如果还有缺工，用idle_workers补充
            if needed > 0 and available_idle_workers:
                # 找到距离gas_site最近的idle_workers
                available_idle_workers.sort(key=lambda w: w.distance_to(gas_site))
            
                assigned = 0
                workers_to_remove = []
                for worker in available_idle_workers:
                    if assigned >= needed:
                        break
                    worker.gather(gas_site)
                    print(f"Assigned idle worker to gas: {gas_site}")
                    workers_to_remove.append(worker)
                    assigned += 1
            
                # 从available_idle_workers中移除已分配的workers
                for worker in workers_to_remove:
                    available_idle_workers.remove(worker)
            
                needed -= assigned
        
            # 更新gas_tasks
            gas_tasks[gas_site] = needed
            if gas_tasks[gas_site] <= 0:
                del gas_tasks[gas_site]

        # 6. 用剩余的idle_workers填补mineral_sites
        for worker in available_idle_workers:
            if not mineral_tasks:
                break
        
            # 选择距离最近的mineral_site
            target = min(mineral_tasks.keys(), key=lambda s: s.distance_to(worker))
            worker.gather(target)
            print(f"Assigned idle worker to mineral: {target}")
        
            # 更新mineral_site的需求
            mineral_tasks[target] -= 1
            if mineral_tasks[target] <= 0:
                del mineral_tasks[target]

        # 7. 如果还有gas_site缺工且还有mineral workers可调配，进行第二轮调配
        if gas_tasks:
            for gas_site in list(gas_tasks.keys()):
                needed = gas_tasks[gas_site]
                if needed <= 0:
                    continue
                
                # 扩大搜索范围，找到更远的mineral workers
                distant_mineral_workers = []
                for mineral in mineral_patches:
                    if mineral.distance_to(gas_site) < 15:  # 扩大搜索范围
                        for worker in self.workers.gathering:
                            if (worker.distance_to(mineral) < 2 and 
                                worker not in available_idle_workers):
                                distant_mineral_workers.append((worker, worker.distance_to(gas_site)))
            
                # 按距离排序
                distant_mineral_workers.sort(key=lambda x: x[1])
            
                # 重新分配
                reassigned = 0
                for worker, _ in distant_mineral_workers:
                    if reassigned >= needed:
                        break
                    worker.gather(gas_site)
                    print(f"Reassigned distant worker from mineral to gas: {gas_site}")
                    reassigned += 1

    async def _deploy_mules(self, mineral_patches) -> None:
        """
        部署MULE到合适的矿点
        """
        mule_units = self.units(UnitTypeId.MULE).idle
        for mule in mule_units:
            nearby_minerals = [m for m in mineral_patches if m.distance_to(mule) < 12]
            best_mineral = self._select_best_mineral_for_mule(nearby_minerals, mule)
            if best_mineral:
                mule.gather(best_mineral)

    def _select_best_mineral_for_mule(self, mineral_patches, orbital_command):
        """
        为MULE选择最佳的矿点
        优先选择：
        1. 资源量较多的矿点
        2. 距离基地较近的矿点
        3. 当前采集单位较少的矿点
        4. 没有MULE的矿点
        """
        if not mineral_patches:
            return None
        
        best_mineral = None
        best_score = -1
        
        for mineral in mineral_patches:
            # 计算该矿点的评分
            score = 0
            
            # 资源量权重（剩余资源越多越好）
            resource_weight = mineral.mineral_contents / 1800  # 1800是矿点的初始资源量
            score += resource_weight * 40
            
            # 距离权重（距离越近越好）
            distance_weight = 1 - (mineral.distance_to(orbital_command) / 12)
            score += distance_weight * 20
            
            # 采集单位数量权重（采集单位越少越好）
            current_harvesters = 0
            for unit in self.units:
                if (hasattr(unit, 'order_target') and unit.order_target == mineral.tag and 
                    unit.type_id in [UnitTypeId.SCV, UnitTypeId.MULE]):
                    current_harvesters += 1
            
            harvester_weight = max(0, 1 - current_harvesters / 4)  # 最多4个采集单位(2个SCV + 2个MULE)
            score += harvester_weight * 30
            
            # 检查是否已经有MULE在这个矿点
            mule_count = 0
            for unit in self.units.filter(lambda u: u.type_id == UnitTypeId.MULE):
                if hasattr(unit, 'order_target') and unit.order_target == mineral.tag:
                    mule_count += 1
                    
            if mule_count >= 1:  # 每个矿点最多1个MULE
                score -= 50
            
            # 优先选择资源量充足的矿点
            if mineral.mineral_contents < 500:  # 资源量过低的矿点降低优先级
                score -= 20
            
            if score > best_score:
                best_score = score
                best_mineral = mineral
        
        return best_mineral if best_score > 0 else None

    def get_terran_suggestions(self):
        suggestions = []
        # 人口不足时建议建造Supply Depot
        if (
            self.supply_left < 5
            and not self.already_pending(UnitTypeId.SUPPLYDEPOT)
            and self._can_build(UnitTypeId.SUPPLYDEPOT)
        ):
            suggestions.append("Supply is low! Build a Supply Depot immediately.")
        # 没有Supply Depot时建议建造
        if (
            self.get_total_amount(UnitTypeId.SUPPLYDEPOT) < 1
            and self._can_build(UnitTypeId.SUPPLYDEPOT)
            and not self.already_pending(UnitTypeId.SUPPLYDEPOT)
        ):
            suggestions.append("At least one Supply Depot is necessary for development, consider building one.")
        # 没有MULE时建议建造
        if (
            self.get_total_amount(UnitTypeId.MULE) < 5
            and not self.already_pending(UnitTypeId.MULE)
            and self.townhalls(UnitTypeId.ORBITALCOMMAND).ready.exists
        ):
            suggestions.append("MULE can boost your economy, consider calling one from your Command Center.")
        # 没有Refinery时建议建造
        if self.get_total_amount(UnitTypeId.REFINERY) < 1 and self._can_build(UnitTypeId.REFINERY):
            suggestions.append("At least one Refinery is necessary for gas collection, consider building one.")
        # 没有Barracks时建议建造
        if (
            self.structures(UnitTypeId.SUPPLYDEPOT).exists
            and self.get_total_amount(UnitTypeId.BARRACKS) < 1
            and self.structures(UnitTypeId.SUPPLYDEPOT).ready.exists
        ):
            suggestions.append("At least one Barracks is necessary for attacking units, consider building one.")
        # 没有Barracks Tech Lab时建议建造
        barracks = self.structures(UnitTypeId.BARRACKS).ready
        if (
            barracks.exists
            and self.get_total_amount(UnitTypeId.BARRACKSTECHLAB) < 1
            and self._can_build(UnitTypeId.BARRACKSTECHLAB)
        ):
            if barracks.idle.exists:
                suggestions.append("At least one Barracks Tech Lab is necessary for advanced units, consider building one.")
            else:
                suggestions.append(
                    "Consider building a Barracks Tech Lab when one of your Barracks is idle to unlock advanced units."
                )
        # Marine数量少于2时建议建造
        if (
            self.structures(UnitTypeId.BARRACKS).ready.exists
            and self.get_total_amount(UnitTypeId.MARINE) < 2
            and self._can_build(UnitTypeId.MARINE)
        ):
            suggestions.append("At least 2 Marines are necessary for defensing, consider training one.")
        # 没有Marauder时建议建造
        if (
            self.structures(UnitTypeId.BARRACKSTECHLAB).ready.exists
            and self.get_total_amount(UnitTypeId.MARAUDER) < 1
            and self._can_build(UnitTypeId.MARAUDER)
        ):
            suggestions.append("At least one Marauder is necessary for defensing, consider training one.")
        # 只有一座Barracks时建议建造第二座
        if self.get_total_amount(UnitTypeId.BARRACKS) == 1 and self._can_build(UnitTypeId.BARRACKS):
            suggestions.append("Consider building a second Barracks to increase unit production.")
        # 如果有2个兵营且没有Factory时建议建造
        if (
            self.structures(UnitTypeId.BARRACKS).ready.amount >= 2
            and self.structures(UnitTypeId.BARRACKSTECHLAB).ready.exists
            and self.get_total_amount(UnitTypeId.FACTORY) == 0
            and self._can_build(UnitTypeId.FACTORY)
        ):
            suggestions.append("Consider building a Factory to unlock mechanical units.")
        # 有Factory时建议升级TechLab
        if (
            self.structures(UnitTypeId.FACTORY).ready.exists
            and self.get_total_amount(UnitTypeId.FACTORYTECHLAB) == 0
            and self._can_build(UnitTypeId.FACTORYTECHLAB)
        ):
            suggestions.append("Consider upgrade Factory Tech Lab to train powerful units.")
        if self.structures(UnitTypeId.FACTORYTECHLAB).ready.exists and self.get_total_amount(UnitTypeId.SIEGETANK) < 3:
            suggestions.append("Consider train Siege Tank to increase your army's firepower.")
        # 建议升级Command Center到Orbital Command
        cc = self.townhalls(UnitTypeId.COMMANDCENTER).ready
        if cc.exists:
            main_cc = cc.first  # 通常主基地优先升级
            if main_cc.is_idle and self._can_build(UnitTypeId.ORBITALCOMMAND) and self.get_total_amount(UnitTypeId.SCV) >= 16:
                suggestions.append("Upgrade Command Center to Orbital Command for better economy.")
        # 如果只有一座Orbital Command且没有Command Center时，建议建造新的Command Center
        if (
            self.get_total_amount(UnitTypeId.ORBITALCOMMAND) == 1
            and self.get_total_amount(UnitTypeId.COMMANDCENTER) == 0
            and self._can_build(UnitTypeId.COMMANDCENTER)
        ):
            suggestions.append("Consider building another Command Center to expand your base at another resource location.")
        # 维持适当的Marine和Marauder比例
        marine_count = self.get_total_amount(UnitTypeId.MARINE)
        marauder_count = self.get_total_amount(UnitTypeId.MARAUDER)

        if marine_count + marauder_count > 10:
            ratio = marauder_count / max(1, marine_count)
            if ratio < 0.5:
                suggestions.append("Increase Marauder production for better tanking.")
            elif ratio > 2.5:
                suggestions.append("Produce more Marines for DPS against light units.")

        return suggestions

    def get_protoss_suggestions(self):
        suggestions = []

        # 人口不足时建议建造Pylon (水晶塔)
        if (
            self.supply_left < 4
            and not self.already_pending(UnitTypeId.PYLON)
            and self._can_build(UnitTypeId.PYLON)
        ):
            suggestions.append("Supply is low! Build a Pylon immediately.")
        
        # 建筑没有能量时建议建造Pylon
        if self.structures.filter(lambda s: not s.is_powered and s.build_progress > 0.1).exists:
             if self._can_build(UnitTypeId.PYLON) and not self.already_pending(UnitTypeId.PYLON):
                suggestions.append("Some of your structures are unpowered! Build a Pylon nearby.")

        # 没有Pylon时建议建造
        if (
            self.get_total_amount(UnitTypeId.PYLON) < 1
            and self._can_build(UnitTypeId.PYLON)
            and not self.already_pending(UnitTypeId.PYLON)
        ):
            suggestions.append("At least one Pylon is necessary for development and power, consider building one.")

        # 有多余能量时建议使用Chrono Boost (星空加速)
        nexus = self.townhalls(UnitTypeId.NEXUS).ready
        if nexus.exists and nexus.first.energy >= 50:
            suggestions.append("Your Nexus has enough energy for Chrono Boost. Use it on the Nexus for more Probes or on a production building.")

        # 没有Assimilator (吸收厂) 时建议建造
        if self.get_total_amount(UnitTypeId.ASSIMILATOR) < 1 and self._can_build(UnitTypeId.ASSIMILATOR):
            suggestions.append("At least one Assimilator is necessary for gas collection, consider building one.")

        # 没有Gateway (传送门) 时建议建造
        if (
            self.structures(UnitTypeId.PYLON).exists
            and self.get_total_amount(UnitTypeId.GATEWAY) < 1
            and self._can_build(UnitTypeId.GATEWAY)
        ):
            suggestions.append("At least one Gateway is necessary for training ground units, consider building one.")

        # 没有Cybernetics Core (控制核心) 时建议建造
        if (
            self.structures(UnitTypeId.GATEWAY).ready.exists
            and self.get_total_amount(UnitTypeId.CYBERNETICSCORE) < 1
            and self._can_build(UnitTypeId.CYBERNETICSCORE)
        ):
            suggestions.append("A Cybernetics Core is necessary to unlock advanced units like Stalkers, consider building one.")

        # 建议研究Warpgate (折跃门) 科技
        cyber_core = self.structures(UnitTypeId.CYBERNETICSCORE).ready
        if (
            cyber_core.exists
            and self.already_pending_upgrade(UpgradeId.WARPGATERESEARCH) == 0
            and self.can_afford(UpgradeId.WARPGATERESEARCH)
        ):
            if cyber_core.idle.exists:
                suggestions.append("Cybernetics Core is ready. Research Warpgate technology to reinforce your army faster.")
            else:
                suggestions.append("Consider researching Warpgate technology when your Cybernetics Core is idle.")
                
        # Zealot (狂热者) 数量少于2时建议建造
        if (
            self.structures(UnitTypeId.GATEWAY).exists
            and self.get_total_amount(UnitTypeId.ZEALOT) < 2
            and self._can_build(UnitTypeId.ZEALOT)
        ):
            suggestions.append("At least 2 Zealots are necessary for early defense, consider training one.")

        # 没有Stalker (追猎者) 时建议建造
        if (
            self.structures(UnitTypeId.CYBERNETICSCORE).ready.exists
            and self.get_total_amount(UnitTypeId.STALKER) < 1
            and self._can_build(UnitTypeId.STALKER)
        ):
            suggestions.append("At least one Stalker is useful for anti-air and kiting, consider training one.")
            
        # 传送门数量不足时建议建造更多
        gateway_count = self.get_total_amount(UnitTypeId.GATEWAY) + self.get_total_amount(UnitTypeId.WARPGATE)
        if 1 <= gateway_count < 3 and self._can_build(UnitTypeId.GATEWAY):
            suggestions.append("Consider building more Gateways to increase unit production.")

        # 维持适当的Zealot和Stalker比例
        zealot_count = self.get_total_amount(UnitTypeId.ZEALOT)
        stalker_count = self.get_total_amount(UnitTypeId.STALKER)

        if zealot_count + stalker_count > 10:
            # 理想比例：大约1个狂热者对应2个追猎者
            ratio = zealot_count / max(1, stalker_count)
            if ratio > 0.8: # 狂热者过多
                suggestions.append("Your army has many Zealots. Produce more Stalkers for ranged support.")
            elif ratio < 0.3: # 追猎者过多
                suggestions.append("Increase Zealot production to create a stronger frontline for your Stalkers.")

        return suggestions

    def get_zerg_suggestions(self):
        suggestions = []
        
        # 人口不足时建议建造Overlord
        if (
            self.supply_left < 3
            and self.supply_cap < 200 # 避免在200人口时仍然提示
            and not self.already_pending(UnitTypeId.OVERLORD)
            and self._can_build(UnitTypeId.OVERLORD)
        ):
            suggestions.append("Supply is low! Morph an Overlord immediately.")

        # 没有Spawning Pool时建议建造
        if (
            self.get_total_amount(UnitTypeId.SPAWNINGPOOL) < 1
            and not self.already_pending(UnitTypeId.SPAWNINGPOOL)
            and self._can_build(UnitTypeId.SPAWNINGPOOL)
        ):
            suggestions.append("A Spawning Pool is required to create Zerglings, build one.")

        # 没有Queen时建议建造
        # 每个基地至少一个女王用于注卵和防御
        if (
            self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and self.get_total_amount(UnitTypeId.QUEEN) < self.townhalls.amount
            and self._can_build(UnitTypeId.QUEEN)
        ):
            suggestions.append("Build a Queen for each Hatchery to inject larva and defend.")

        # 有女王但基地没有注卵时建议注卵
        queens_with_energy = self.units(UnitTypeId.QUEEN).filter(lambda q: q.energy >= 25)
        hatcheries_needing_inject = self.townhalls.ready.filter(lambda h: not h.has_buff(BuffId.QUEENSPAWNLARVATIMER))
        if queens_with_energy.exists and hatcheries_needing_inject.exists:
            suggestions.append("Your Queen has energy! Use 'Inject Larva' on a Hatchery to boost production.")

        # 没有Extractor时建议建造
        if self.get_total_amount(UnitTypeId.EXTRACTOR) < 1 and self._can_build(UnitTypeId.EXTRACTOR):
            suggestions.append("At least one Extractor is necessary for gas collection, consider building one.")

        # Zergling数量少于6时建议建造
        if (
            self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and self.get_total_amount(UnitTypeId.ZERGLING) < 6
            and self._can_build(UnitTypeId.ZERGLING)
        ):
            suggestions.append("At least 6 Zerglings are necessary for early defense, consider training some.")

        # 建议扩张（建造第二个基地）
        if self.townhalls.amount < 2 and self._can_build(UnitTypeId.HATCHERY):
            suggestions.append("Consider building a second Hatchery to expand your economy and production.")

        # 建议建造Roach Warren
        if (
            self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and self.get_total_amount(UnitTypeId.ROACHWARREN) == 0
            and self._can_build(UnitTypeId.ROACHWARREN)
        ):
            suggestions.append("Consider building a Roach Warren to unlock Roaches, a strong armored unit.")

        # 没有Roach时建议建造
        if (
            self.structures(UnitTypeId.ROACHWARREN).ready.exists
            and self.get_total_amount(UnitTypeId.ROACH) < 5
            and self._can_build(UnitTypeId.ROACH)
        ):
            suggestions.append("Roaches are strong against many early units, consider training some.")

        # 建议升级到Lair (T2科技)
        if (
            self.structures(UnitTypeId.SPAWNINGPOOL).ready.exists
            and self.get_total_amount(UnitTypeId.LAIR) == 0
            and self.townhalls(UnitTypeId.HATCHERY).idle.exists
            and self._can_build(UnitTypeId.LAIR)
        ):
            suggestions.append("Upgrade a Hatchery to a Lair to unlock powerful mid-game units and upgrades.")

        # 有Lair时建议建造Hydralisk Den
        if (
            self.structures(UnitTypeId.LAIR).ready.exists
            and self.get_total_amount(UnitTypeId.HYDRALISKDEN) == 0
            and self._can_build(UnitTypeId.HYDRALISKDEN)
        ):
            suggestions.append("Build a Hydralisk Den to unlock Hydralisks, a versatile ranged unit.")

        # 有Hydralisk Den时建议训练Hydralisk
        if self.structures(UnitTypeId.HYDRALISKDEN).ready.exists and self.get_total_amount(UnitTypeId.HYDRALISK) < 5:
            suggestions.append("Consider training Hydralisks to strengthen your army's anti-air and ranged capabilities.")

        # 维持适当的Zergling和Roach比例
        zergling_count = self.get_total_amount(UnitTypeId.ZERGLING)
        roach_count = self.get_total_amount(UnitTypeId.ROACH)

        if zergling_count + roach_count > 20:
            # 计算蟑螂在(蟑螂+小狗)部队中的价值占比，蟑螂占2人口，小狗占0.5
            roach_supply = roach_count * 2
            zergling_supply = zergling_count * 0.5
            total_supply = roach_supply + zergling_supply
            
            if total_supply > 0:
                roach_ratio = roach_supply / total_supply
                if roach_ratio < 0.3: # 蟑螂占比过低
                    suggestions.append("Your army is Zergling-heavy. Add Roaches for a stronger frontline.")
                elif roach_ratio > 0.8: # 蟑螂占比过高
                    suggestions.append("Your army is Roach-heavy. Add Zerglings for more DPS and to surround enemies.")

        return suggestions
    
    def get_suggestions(self):
        suggestions = []

        # 发现敌人单位时建议攻击
        if self.enemy_units.exists:
            n_enemies = len(
                [unit for unit in self.enemy_units if unit.name not in ["Probe", "SCV", "Drone", "MULE", "Overlord"]]
            )
            if n_enemies > 0:
                suggestions.append(
                    f"Enemy units detected ({n_enemies} units), consider attacking them."
                )

        if self.time < 300 and self.time > 60:
            suggestions.append("The enemy will start a fierce attack at 03:00, so you need to start producing a large number of attack units, such as Marauder, at least at 02:30.")
        
        if self.time > 300 and self.supply_army > 15 and len(self.enemy_units) < 8:
            suggestions.append("We can win the game right away! Please find and eliminate all enemies as soon as possible.")
        
        if self.minerals >= 500:
            suggestions.append("Too much minerals! Consider spending them on expanding or developing high technology.")

        if self.config.own_race == "Terran":
            suggestions.extend(self.get_terran_suggestions())
        elif self.config.own_race == "Protoss":
            suggestions.extend(self.get_protoss_suggestions())
        elif self.config.own_race == "Zerg":
            suggestions.extend(self.get_zerg_suggestions())

        return suggestions

    def log_current_iteration(self, iteration: int):
        print(f"================ iteration {iteration} ================")
        self.logging("iteration", iteration, save_trace=True)
        self.logging("time_seconds", int(self.time), save_trace=True)
        self.logging("minerals", self.minerals, save_trace=True)
        self.logging("vespene", self.vespene, save_trace=True)
        
        unit_mineral_value, unit_vespene_value = 0, 0
        for unit in self.units:
            unit_value = self.calculate_unit_value(unit.type_id)
            unit_mineral_value += unit_value.minerals
            unit_vespene_value += unit_value.vespene
        self.logging("unit_mineral_value", unit_mineral_value, save_trace=True)
        self.logging("unit_vespene_value", unit_vespene_value, save_trace=True)
        
        structure_mineral_value, structure_vespene_value = 0, 0
        for structure in self.structures:
            structure_value = self.calculate_unit_value(structure.type_id)
            structure_mineral_value += structure_value.minerals
            structure_vespene_value += structure_value.vespene
        self.logging("structure_mineral_value", structure_mineral_value, save_trace=True)
        self.logging("structure_vespene_value", structure_vespene_value, save_trace=True)
        
        self.logging("supply_army", self.supply_army, save_trace=True)
        self.logging("supply_workers", self.supply_workers, save_trace=True)
        self.logging("supply_left", self.supply_left, save_trace=True)
        self.logging("n_structures", len(self.structures), save_trace=True)
        self.logging("n_visible_enemy_units", len(self.enemy_units), save_trace=True)
        self.logging("n_visible_enemy_structures", len(self.enemy_structures), save_trace=True)
        unit_types = set(unit.type_id for unit in self.units)
        structure_types = set(unit.type_id for unit in self.structures)
        self.logging("n_unit_types", len(unit_types), save_trace=True)
        self.logging("n_structure_types", len(structure_types), save_trace=True)

#### shy ####
    def get_enemy_units_near_structures(self, distance: float=15.0):  
        """  
        筛选出距离任意我方建筑在指定距离内的敌方单位  
        
        :param distance: 距离阈值  
        :return: 符合条件的敌方单位集合  
        """  
        if not self.structures:  
            # 如果没有建筑,返回空集合  
            return self.enemy_units.subgroup([])  
        
        if not self.enemy_units:  
            # 如果没有敌方单位,返回空集合  
            return self.enemy_units  
        
        # 使用 in_distance_of_group 方法筛选  
        return self.enemy_units.in_distance_of_group(self.structures, distance)
    

    async def automatic_defense(self, base_defense_radius: float = 10.0, response_radius: float = 20.0):
        """
        自动指派战斗单位去防御基地。
        [新] 使用 self.active_defense_map {defender_tag: enemy_tag} 来跟踪防御/威胁对。
        当一个特定的威胁消失（死亡或离开）时，对应的防御单位会停止追击。
        """

        # 1. 查找所有可用于防御的单位
        non_combat_types = {"SCV", "PROBE", "DRONE", "MULE"}
        available_defenders = self.units.filter(
            lambda unit: unit.can_attack and unit.name.upper() not in non_combat_types
        )

        # 2. 查找进入基地防御圈的敌人
        enemies_near_base = self.get_enemy_units_near_structures(base_defense_radius)
        # 当前在防御圈内的所有敌人 tag
        current_threat_tags = enemies_near_base.tags
        # 视野中所有存活的敌人 tag
        all_living_enemy_tags = self.enemy_units.tags 

        # 3. [Check] 维护和清理 self.active_defense_map
        # 遍历当前的防御记录，判断是否终止
        new_defense_map = {} # 用一个新的 map 来存储仍然有效的防御记录
        stopped_units = 0

        # 遍历当前记录在案的“我方防御单位”和“其锁定的敌方单位”
        for defender_tag, enemy_tag in self.active_defense_map.items():
            defender = self.units.find_by_tag(defender_tag)

            # 检查我方防御单位是否存活
            if not defender:
                continue # 防御单位死亡，自动从 new_map 中移除，无需操作

            # 检查敌方单位是否存活
            enemy_is_alive = enemy_tag in all_living_enemy_tags
            if not enemy_is_alive:
                # [判断是否终止] 对应的敌方单位消失（死亡）
                continue # 敌方单位死亡，防御任务结束，自动移除

            # 检查敌方单位是否仍在威胁区
            enemy_is_still_a_threat = enemy_tag in current_threat_tags

            if enemy_is_still_a_threat:
                # 敌人存活且仍在威胁区，保持防御记录
                new_defense_map[defender_tag] = enemy_tag
            else:
                # [判断是否终止] 敌人存活，但已离开威胁区
                # 停止对应的防御单位
                if defender.orders and defender.orders[0].ability.id in {AbilityId.ATTACK, AbilityId.ATTACK_ATTACK}:
                    defender.stop()
                    stopped_units += 1
                # 不将此记录添加到 new_map 中，防御结束

        self.active_defense_map = new_defense_map # [删除防御记录] 更新 map，移除了所有已完成的防御

        if stopped_units > 0:
            print(f"Automatic Defense: Stopped {stopped_units} units from pursuit as their targets left.")
            await self.chat_send(f"Automatic Defense: {stopped_units} units disengaging.")

        # 4. [指派新的防御]
        if not enemies_near_base.exists:
            return # 基地安全，也无需指派新单位

        threat_center = enemies_near_base.center
        # 找出在响应范围内的所有可用单位
        responding_units = available_defenders.closer_than(response_radius, threat_center)

        if not responding_units.exists:
            return # 没有单位能响应

        # 找出尚未分配任务的单位
        unassigned_defenders = responding_units.filter(lambda u: u.tag not in self.active_defense_map)

        new_assignments = 0
        for unit in unassigned_defenders:
            # 命令每个单位攻击距离它自己最近的那个威胁
            target = enemies_near_base.closest_to(unit)
            if target:
                unit.attack(target)
                # [记录防御行为]
                # 将“我方单位”和“它去攻击的敌方单位”绑定
                self.active_defense_map[unit.tag] = target.tag
                new_assignments += 1

        if new_assignments > 0:
            print(f"Automatic Defense: Assigned {new_assignments} new units to defend.")
            await self.chat_send(f"Automatic Defense: Engaging base threat!")

    async def set_terran_combat_rally_points(self):
            """
            为每个人族攻击单位生产建筑（兵营、工厂、星港）设置集结点。
            
            集结点基于最近的指挥中心（或其升级形态），
            设置在该指挥中心与地图中心连线上，距离指挥中心15个单位的位置。
            [V2: 使用 self.structure_rally_points 缓存避免重复设置]
            """
            
            # 1. 定义人族的相关建筑
            
            # (A) 生产攻击单位的建筑
            combat_structures = {
                UnitTypeId.BARRACKS,
                UnitTypeId.FACTORY,
                UnitTypeId.STARPORT,
            }

            # (B) 用来计算集结点的"中心"建筑 (指挥中心及其升级)
            rally_centers = {
                UnitTypeId.COMMANDCENTER,
                UnitTypeId.ORBITALCOMMAND,
                UnitTypeId.PLANETARYFORTRESS
            }

            # 2. 找到所有已建成的"集结中心"建筑 (B)
            all_ready_rally_centers = self.structures(rally_centers).ready
            
            if not all_ready_rally_centers:
                # 如果一个"中心"都没有，则无法设置
                return

            # 3. 获取地图中心
            map_center = self.game_info.map_center

            # 4. 预先计算所有"集结中心"的集结点
            rally_points_by_center_tag = {}
            for center in all_ready_rally_centers:
                rally_point = center.position.towards(map_center, 15)
                rally_points_by_center_tag[center.tag] = rally_point

            # 5. [新增] 获取当前所有相关生产建筑
            ready_combat_structures = self.structures(combat_structures).ready
            current_combat_tags = {s.tag for s in ready_combat_structures}
            
            # 6. [新增] 清理缓存中已不存在(被摧毁)的建筑
            # (使用 list() 来允许在迭代时修改字典)
            tags_to_remove = [tag for tag in self.structure_rally_points if tag not in current_combat_tags]
            for tag in tags_to_remove:
                del self.structure_rally_points[tag]
                print(f"Rally cache: Removed destroyed structure [Tag:{tag}]")

            # 7. 遍历所有已建成的生产建筑 (A)，并设置集结点
            for structure in ready_combat_structures:
                
                # 找到最近的"集结中心" (B)
                closest_center = all_ready_rally_centers.closest_to(structure.position)
                
                # 获取这个"中心"对应的集结点
                target_rally_point = rally_points_by_center_tag[closest_center.tag]
                
                # 8. [修改] 检查缓存的集结点是否与目标集结点不同
                cached_point = self.structure_rally_points.get(structure.tag)

                if cached_point != target_rally_point:
                    
                    # *** (1) 执行设置集结点的动作 ***
                    structure(AbilityId.RALLY_UNITS, target_rally_point)
                    
                    # *** (2) [新增] 更新缓存 ***
                    self.structure_rally_points[structure.tag] = target_rally_point
                    
                    # *** (3) 按照您的要求，发送通知 ***
                    message = (
                        f"已更新集结点: {structure.type_id.name} [Tag:{structure.tag}] "
                        f"-> {target_rally_point.rounded} "
                        f"(基于 {closest_center.type_id.name} [Tag:{closest_center.tag}])"
                    )
                    print(message)
                    await self.chat_send(message)

    def _clamp_position_to_map_bounds(self, p: Point2) -> Point2:
            """ 
            确保坐标点在地图边界内，且 X 和 Y 至少为 1。 
            """
            map_width = self.game_info.map_size.width
            map_height = self.game_info.map_size.height
            
            # 用户要求：如果计算出 0 或者 负数位置 取 1
            x = max(1, p.x)
            y = max(1, p.y)
            
            # 额外保护：确保不会超出地图边界 (减1是因为坐标从0开始)
            x = min(x, map_width - 1)
            y = min(y, map_height - 1)
            
            return Point2((x, y))

    async def manage_garrison(self):
            """
            管理基地的自动驻防。
            为每个基地计算4个驻防点，并按比例分配防区内的空闲战斗单位。
            [修改] 只有在基地 10.0 半径内没有敌情时才会执行。
            """
            
            # 1. [修改] 检查是否有敌情 (使用 automatic_defense 的 10.0 半径)
            # 任何建筑附近 10.0 范围内有敌人，则跳过驻防调整
            enemies_near_base = self.get_enemy_units_near_structures(10.0) 
            if enemies_near_base.exists:
                # print("敌情威胁中，暂停驻防调整。")
                return # 存在敌情，不调整驻防

            # 2. 获取所有空闲战斗单位
            non_combat_types = {UnitTypeId.SCV, UnitTypeId.PROBE, UnitTypeId.DRONE, UnitTypeId.MULE}
            idle_combat_units = self.units.filter(
                lambda u: u.can_attack 
                and u.type_id not in non_combat_types 
                and u.is_idle
            )
            if not idle_combat_units.exists:
                # print("没有空闲的战斗单位，跳过驻防。")
                return # 没有单位可分配

            # 3. 遍历每个基地 (center) 来管理其驻防
            rally_centers_types = {
                UnitTypeId.COMMANDCENTER,
                UnitTypeId.ORBITALCOMMAND,
                UnitTypeId.PLANETARYFORTRESS
            }
            all_ready_rally_centers = self.structures(rally_centers_types).ready
            if not all_ready_rally_centers.exists:
                return # 没有基地
            
            map_center = self.game_info.map_center
            total_units_moved = 0

            for center in all_ready_rally_centers:
                
                # --- A. 计算 4 个驻防点 ---
                
                # 逻辑同 set_terran_combat_rally_points
                main_rally_point = center.position.towards(map_center, 15)
                
                # Point 1 (50%): The main rally point
                p1 = main_rally_point
                
                # Point 2 (10%): 在 指挥中心(center) 和 集结点(p1) 连线上，靠近指挥中心
                p2 = main_rally_point.towards(center.position, self.GARRISON_EXTENSION_DISTANCE)
                
                # [修改] Points 3 & 4 (20% each): 在 指挥中心(center) 和 P1(main_rally_point) 连线的中点 做垂线
                vector = main_rally_point.position - center.position

                # [修复] 替换 .lerp()，手动计算中点 (P1 + P2) / 2
                mid_point = (center.position + main_rally_point.position) / 2 # 计算中点
                
                if not vector.length: # 避免除零
                    perp_vector = Point2((self.GARRISON_PERPENDICULAR_DISTANCE, 0))
                else:
                    # [修复] 替换 .rotated(90)，手动计算垂直向量
                    # 2D 向量 (x, y) 旋转 90 度为 (-y, x)
                    norm_vec = vector.normalized
                    rotated_vec = Point2((-norm_vec.y, norm_vec.x))
                    perp_vector = rotated_vec * self.GARRISON_PERPENDICULAR_DISTANCE
                    
                p3 = mid_point + perp_vector
                p4 = mid_point - perp_vector

                # [新增] 确保所有驻防点坐标合法（大于0且在地图内）
                p1 = self._clamp_position_to_map_bounds(p1)
                p2 = self._clamp_position_to_map_bounds(p2)
                p3 = self._clamp_position_to_map_bounds(p3)
                p4 = self._clamp_position_to_map_bounds(p4)
                
                # [修改] 按优先级排序：P1, P2, P3, P4
                garrison_quotas_points = [
                    (0.50, p1), # P1 (50%)
                    (0.10, p2), # P2 (10%)
                    (0.20, p3), # P3 (20%)
                    (0.20, p4)  # P4 (20%)
                ]

                # --- B. 分配单位 ---
                
                # 1. 获取防区内的所有空闲单位 (只操作本防区的兵力)
                # [注意] GARRISON_DEFENSE_ZONE_RADIUS (30) 仍然用于定义 *我方* 防区范围
                units_in_zone = idle_combat_units.closer_than(self.GARRISON_DEFENSE_ZONE_RADIUS, center.position)
                total_units_in_zone = units_in_zone.amount
                
                if total_units_in_zone == 0:
                    continue # 这个基地防区没兵，跳过

                available_units = list(units_in_zone) # 可供分配的单位
                units_moved_this_base = 0

                # 2. 按优先级遍历驻防点
                for quota, point in garrison_quotas_points:
                    needed_count = math.ceil(total_units_in_zone * quota)
                    
                    # 检查有多少单位 *已经* 在这个点
                    units_at_point = []
                    remaining_available = []
                    
                    for u in available_units:
                        if u.distance_to(point) <= self.GARRISON_CHECK_RADIUS:
                            units_at_point.append(u)
                        else:
                            remaining_available.append(u)
                    
                    num_at_point = len(units_at_point)
                    num_to_move = max(0, needed_count - num_at_point)
                    
                    # a. 将已在位置的单位(最多needed_count个)标记为“已分配”
                    # (我们只关心更新 available_units 列表)
                    # final_assigned_at_point = units_at_point[:needed_count]
                    
                    # b. 将多余的单位 和 不在位置的单位 放回“可用”池
                    excess_units = units_at_point[needed_count:]
                    available_units = remaining_available + excess_units
                    
                    # c. 如果还需要单位，从“可用”池中调拨 (如果兵力不够, available_units会变空, 自动满足 P1 > P2 > P3 > P4)
                    if num_to_move > 0 and available_units:
                        # 排序，找到最近的
                        available_units.sort(key=lambda u: u.distance_to(point))
                        
                        units_to_move = available_units[:num_to_move]
                        available_units = available_units[num_to_move:] # 更新“可用”池
                        
                        for u in units_to_move:
                            u.move(point)
                            units_moved_this_base += 1
                
                # 3. 将所有剩余未分配的单位派往主集结点 (P1)
                for u in available_units:
                    if u.distance_to(p1) > self.GARRISON_CHECK_RADIUS:
                        u.move(p1)
                        units_moved_this_base += 1
                
                if units_moved_this_base > 0:
                    print(f"Garrisoning Base {center.tag}: Reassigned {units_moved_this_base} units.")
                    total_units_moved += units_moved_this_base


# --- 侦察逻辑 (V3: 连续侦察 & 最近目标) ---

    def _update_scouting_information(self):
        """
        [V3] 主动检查视野, 记录新发现的单位。
        (根据用户需求, 移除了 "丢失视野" 的打印)
        此函数不重写 BotAI 事件, 而是由 manage_scouting 调用。
        """
        
        current_visible_tags = {u.tag for u in self.enemy_units}
        
        # --- 1. 处理新进入视野的单位 ---
        new_tags = current_visible_tags - self.known_enemy_tags_in_vision
        
        if new_tags:
            current_time_int = int(self.time)
            if current_time_int not in self.scouting_information:
                self.scouting_information[current_time_int] = []

            for tag in new_tags:
                unit = self.enemy_units.find_by_tag(tag)
                if unit:
                    info = f"发现 {unit.type_id.name} (Tag: {unit.tag}) 位于 {unit.position.rounded}"
                    if unit.is_structure:
                        info += " (建筑)"
                    
                    # [V3] 侦察报告只打印新发现的单位
                    print(f"[侦察信息] {info}")
                    
                    # 添加到我们的信息字典中
                    self.scouting_information[current_time_int].append(info)
                    
                    # 额外记录到日志
                    self.logging("scout_discovery", info)

        # --- 2. 处理离开视野的单位 (V3: 根据用户要求, 不再打印 "丢失视野") ---
        # lost_tags = self.known_enemy_tags_in_vision - current_visible_tags
        # 
        # for tag in lost_tags:
        #     # 从 BotAI 内部访问上一帧的单位地图
        #     last_known_unit = (
        #         self._enemy_units_previous_map.get(tag) or
        #         self._enemy_structures_previous_map.get(tag)
        #     )
        #     if last_known_unit:
        #         print(f"[侦察信息] 丢失 {last_known_unit.type_id.name} 的视野, 最后位置 {last_known_unit.position.rounded}")

        # --- 3. 更新状态 ---
        self.known_enemy_tags_in_vision = current_visible_tags


    async def manage_scouting(self):
        """
        管理侦察单位和信息收集。
        在每个 on_step 周期被 run 方法调用。
        """
        
        # [V3] 首先, 更新我们的视野信息
        self._update_scouting_information()
        
        # 1. 检查当前侦察单位的状态
        if self.active_scout_unit_tag:
            scout_unit = self.units.find_by_tag(self.active_scout_unit_tag)
            
            # 侦察单位死亡或消失
            if not scout_unit: 
                print(f"侦察单位 (Tag: {self.active_scout_unit_tag}) 丢失 (判定为死亡).")
                self.active_scout_unit_tag = None
                self.scout_target_location = None
            
            # [V3 修改] 侦察单位变为空闲 (已到达目的地), 立即派遣到下一个最近的地点
            elif scout_unit.is_idle: 
                print(f"侦察单位 {scout_unit.type_id.name} (Tag: {self.active_scout_unit_tag}) 已变为空闲, 寻找下一个目标...")
                
                # [V3 新] 寻找离侦察兵 *当前位置* 最近的下一个目标
                next_target = self._get_scout_target(from_position=scout_unit.position)
                
                if next_target:
                    # [V3 新] 派遣到新目标
                    self._send_scout(scout_unit, next_target)
                else:
                    # [V3 新] 找不到目标, 释放侦察兵
                    print(f"侦察单位 {scout_unit.type_id.name} (Tag: {self.active_scout_unit_tag}) 已完成所有任务, 释放。")
                    self.active_scout_unit_tag = None
                    self.scout_target_location = None
            
            # 否则, 单位还在路上, 什么都不做
            else:
                pass 

        # 2. 如果当前没有侦察单位, 尝试派遣一个新的
        if not self.active_scout_unit_tag:
            await self._find_and_dispatch_scout()

    async def _find_and_dispatch_scout(self):
        """
        按照 飞机 > Marine > SCV 的优先级, 寻找一个空闲单位并派遣它。
        """
        
        # [V3 修改] 默认从基地出发寻找第一个目标
        target = self._get_scout_target(from_position=self.start_location)
        if not target:
            # print("没有需要侦察的目标。")
            return # 没有可侦察的目标

        scout_unit = None

        # 优先级 3: 飞机 (任何空闲的飞行单位)
        air_types = {UnitTypeId.VIKINGFIGHTER, UnitTypeId.BANSHEE, UnitTypeId.MEDIVAC, UnitTypeId.LIBERATOR, UnitTypeId.RAVEN}
        idle_aircraft = self.units.filter(lambda u: u.type_id in air_types and u.is_idle)
        if idle_aircraft.exists:
            scout_unit = idle_aircraft.random
            print("派遣 [飞机] 执行侦察任务。")

        # 优先级 2: Marine (如果没有可用的飞机)
        if not scout_unit:
            idle_marines = self.units(UnitTypeId.MARINE).idle
            if idle_marines.exists:
                scout_unit = idle_marines.random
                print("派遣 [Marine] 执行侦察任务。")

        # 优先级 1: SCV (仅限开局一次)
        if not scout_unit and not self.scv_scout_sent and self.time > 15:
            scv_pool = self.workers.idle  # <-- 1. 优先尝试找空闲的
            if not scv_pool.exists:
                # <-- 2. 如果找不到空闲的, 就从矿工里找
                scv_pool = self.workers.filter(
                    lambda w: w.is_gathering 
                    and w.order_target in self.mineral_field.tags
                )
            if scv_pool.exists: # <-- 3. 只要池子里有单位 (无论是空闲的还是采矿的)
                scout_unit = scv_pool.random
                self.scv_scout_sent = True
                print("派遣 [SCV] (从工人中抽取)...")
        
        # [V3 修正] 
        # 统一下发派遣命令 (原代码中此缩进错误, 导致飞机和Marine无法被派遣)
        if scout_unit:
            self._send_scout(scout_unit, target)

    def _get_scout_target(self, from_position=None):
        """
        决定下一个侦察目标点。
        [V3 修改] 增加 from_position 参数, 用于计算最近距离。
        
        优先级:
        1. 未侦察过的敌方出生点。
        2. 未侦察过的矿区 (优先去近的)。
        3. 已知的敌方基地 (如果所有点都侦察过了)。
        """
        
        # [V3 新] 确定距离计算的基准点
        base_position = from_position if from_position else self.start_location
        
        # 1. 敌方出生点
        if self.enemy_start_locations:
            enemy_start_loc = self.enemy_start_locations[0]
            if enemy_start_loc not in self.scouted_locations:
                return enemy_start_loc
        
        # 2. 未侦察过的矿区
        unscouted_expansions = []
        for exp_loc in self.expansion_locations_list:
            if exp_loc not in self.scouted_locations:
                unscouted_expansions.append(exp_loc)

        if unscouted_expansions:
            # [V3 修改] 返回距离 (base_position) 最近的那个未侦察矿区
            return min(unscouted_expansions, key=lambda loc: loc.distance_to(base_position))
        
        # 3. 如果所有点都侦察过了, 重置侦察列表, 重新侦察敌方基地
        if self.enemy_start_locations:
            print("所有侦察点已完成, 循环重置。")
            self.scouted_locations.clear() 
            return self.enemy_start_locations[0]

        # 备用方案 (几乎不会触发)
        return self.game_info.map_center

    def _send_scout(self, unit, target):
        """
        发送侦察单位并更新追踪变量的辅助函数。
        """
        unit.move(target)
        self.active_scout_unit_tag = unit.tag
        self.scout_target_location = target
        
        # 当我们派遣单位时就标记该地点, 防止重复派遣
        self.scouted_locations.add(target) 
        
        print(f"正在派遣 {unit.type_id.name} (Tag: {unit.tag}) 侦察 {target.rounded}")
        self.logging("scouting", f"Dispatching {unit.type_id.name} to {target.rounded}")


    # --- 攻击逻辑 (V1: 多风格攻击) ---

    def _get_all_combat_units(self):
        """
        辅助函数: 获取所有非农民、非MULE的战斗单位
        """
        non_combat_types = {"SCV", "PROBE", "DRONE", "MULE"}
        return self.units.filter(
            lambda unit: unit.can_attack and unit.name.upper() not in non_combat_types
        )

    # def manage_kiting_attack(self, units_to_check):
    #     """
    #     为指定的单位列表（units_to_check）应用主动攻击（Kiting/集火）逻辑。
    #     :param units_to_check: 一个单位列表或 sc2.units.Units 集合。
    #     """
        
    #     # 遍历从外部传入的特定单位列表
    #     for unit in units_to_check:
            
    #         # 排除 MULE 和正在建造的 SCV
    #         if unit.type_id in [UnitTypeId.MULE] or unit.is_constructing_scv:
    #             continue

    #         # --- 主动攻击逻辑 (Kiting / 优先集火) ---
    #         enemies_in_range = self.enemy_units.in_attack_range_of(unit)
            
    #         if enemies_in_range.exists:
    #             # (假设 self.get_lowest_health_enemy 是你类中的一个辅助函数)
    #             target = self.get_lowest_health_enemy(enemies_in_range)
    #             if target:
    #                 # 命令该单位攻击这个血量最低的目标
    #                 unit.attack(target)

    def rally_units_to_point(self, units_to_rally, target_point):
        """
        将一个 Units 集合中的所有单位集结 (A-Move) 到一个目标点。

        :param units_to_rally: 一个 sc2.units.Units 集合 (由调用方确保)
        :param target_point: sc2.position.Point2 目标点
        """
        
        # 既然 units_to_rally 总是 Units 集合，
        # 我们可以直接检查 .exists 属性 (这是 Units 集合特有的)
        # 以确保集合非空，防止发送无效指令。
        if units_to_rally.exists:
            
            # 使用 A-Move (攻击性移动)
            # 【修复】 必须遍历集合中的每一个单位
            for unit in units_to_rally:
                unit.attack(target_point)
            
            # --- 备选方案 ---
            # 如果你只想让它们“移动” (M-Move)，忽略敌人，
            # 使用下面这行代替:
            # units_to_rally.move(target_point)


    # 原版: def launch_total_attack(self, target_enemy_unit, units_to_launch):
    def launch_total_attack(self, target, units_to_launch):  
        """
        ...
        :param target: sc2.unit.Unit 或 sc2.position.Point2 - 最终进攻的目标
        ...
        """
        self.logger.info(f"launch_total_attack: 收到总攻发起指令！...")
        
        if not units_to_launch.exists:
            ...
            
        rally_point = self.game_info.map_center
        self.rally_units_to_point(units_to_launch, rally_point)

        self.total_attack_wave_id_counter += 1
        wave_id = self.total_attack_wave_id_counter
        
        unit_tags_set = {unit.tag for unit in units_to_launch}

        # --- 新增逻辑：区分 Unit 和 Point2 ---
        target_tag = None
        final_position = None
        if isinstance(target, Point2):
            final_position = target
        elif isinstance(target, Unit): # 假设 target 是 Unit 对象
            target_tag = target.tag
            final_position = target.position
        else:
            # 备用，防止传入错误类型
            self.logger.error(f"launch_total_attack 收到未知的目标类型: {type(target)}")
            final_position = self.enemy_start_locations[0]
        # --- 结束 ---

        self.total_attack_groups[wave_id] = {
            "unit_tags": unit_tags_set,
            "target_tag": target_tag,         # 最终目标 (可能为 None)
            "final_position": final_position, # (新) 最终目标坐标
            "rally_point": rally_point,      
            "state": "GATHERING"         
        }
        
        self.logger.info(f"launch_total_attack: 已创建总攻波次 {wave_id}，状态: GATHERING。")

    def manage_total_attack_groups(self):
                """
                【维护总攻编队】(在 on_step 中每帧调用)
                
                (V4: 严格按优先级索敌)
                1. (剔除死亡)
                2. (删除全灭记录)
                3. (状态机)
                - if GATHERING: ...
                - if ATTACKING: (新逻辑) 检查主目标是否存活。
                    - (是) (部队A-Move中，无需干预)
                    - (否) 整个小队自动索敌:
                        - 1. 优先 A-Move 离小队中心最近的【建筑】。
                        - 2. 如果没有建筑, 再 A-Move 离小队中心最近的【单位】。
                """
                if not self.total_attack_groups:
                    return

                current_alive_unit_tags = {unit.tag for unit in self.units}
                waves_to_delete = []
                
                # (已移除 Kiting 逻辑)

                for wave_id, attack_data in list(self.total_attack_groups.items()):
                    
                    group_unit_tags = attack_data["unit_tags"]
                    
                    # --- 1. 剔除死亡单位 ---
                    dead_tags = group_unit_tags - current_alive_unit_tags
                    if dead_tags:
                        group_unit_tags.difference_update(dead_tags)

                    # --- 2. 检查编队是否全灭 ---
                    if not group_unit_tags:
                        waves_to_delete.append(wave_id)
                        self.logger.info(f"manage_total_attack_groups: 总攻波次 {wave_id} 已全灭。")
                        continue

                    # --- 3. 状态机逻辑 ---
                    
                    # 获取这个波次所有“存活”的单位对象
                    live_units_in_group = self.units.filter(lambda u: u.tag in group_unit_tags)
                    if not live_units_in_group.exists:
                        continue # (安全检查)

                    current_state = attack_data["state"]

                    # --- 状态: GATHERING (集结中) ---
                    if current_state == "GATHERING":
                        rally_point = attack_data["rally_point"]
                        
                        units_not_at_rally = live_units_in_group.further_than(10.0, rally_point)
                        
                        if not units_not_at_rally.exists:
                            # --- (触发) 所有单位都已抵达集结点 ---
                            self.logger.info(f"总攻波次 {wave_id}: 集结完毕，发动进攻！")
                            attack_data["state"] = "ATTACKING"
                            
                            # --- 【V3 修复：Plan B 逻辑】 ---
                            final_target_pos = attack_data.get("final_position")
                            if not final_target_pos:
                                target_tag = attack_data.get("target_tag")
                                final_target = None
                                if target_tag:
                                    if self.enemy_units.exists:
                                        final_target = self.enemy_units.find_by_tag(target_tag)
                                    if not final_target and self.enemy_structures.exists:
                                        final_target = self.enemy_structures.find_by_tag(target_tag)
                                
                                if final_target:
                                    final_target_pos = final_target.position
                                else:
                                    self.logger.warning(f"总攻波次 {wave_id}: 目标丢失, A-Move至敌方出生点。")
                                    final_target_pos = self.enemy_start_locations[0]
                                    
                            # 统一 A-Move 到最终坐标
                            for unit in live_units_in_group:
                                unit.attack(final_target_pos)
                            # --- 【V3 修复结束】 ---
                        
                        else:
                            # --- (维持) 仍在集结中 ---
                            idle_and_lost = live_units_in_group.idle.further_than(10.0, rally_point)
                            if idle_and_lost.exists:
                                self.rally_units_to_point(idle_and_lost, rally_point)

                    # --- 状态: ATTACKING (进攻中) ---
                    elif current_state == "ATTACKING":
                        
                        # --- 【!! 自动切换目标 V2 !!】 ---
                        
                        target_tag = attack_data.get("target_tag")
                        target_alive = False
                        
                        if target_tag:
                            # 检查目标是否在可见的敌方单位或建筑中
                            if self.enemy_units.find_by_tag(target_tag):
                                target_alive = True
                            elif self.enemy_structures.find_by_tag(target_tag):
                                target_alive = True
                        
                        # 如果原定目标 (target_tag) 已被消灭 (或不存在)
                        if not target_alive:
                            self.logger.info(f"总攻波次 {wave_id}: 原目标 {target_tag} 丢失, 自动寻找新目标。")
                            
                            # 1. 确定攻击小队的中心点
                            squad_center = live_units_in_group.center
                            
                            # 2. 寻找最近的敌方目标 (【V4 修改】 优先建筑，然后单位)
                            new_target = None
                            
                            # 【V4】 按照用户要求: 分别判断，优先建筑
                            if self.enemy_structures.exists:
                                # 优先：寻找最近的建筑
                                new_target = self.enemy_structures.closest_to(squad_center)
                            elif self.enemy_units.exists:
                                # 其次：寻找最近的单位
                                new_target = self.enemy_units.closest_to(squad_center)
                            
                            # 3. 如果找到了新目标
                            if new_target:
                                self.logger.info(f"总攻波次 {wave_id}: 锁定新目标 {new_target.name} (Tag: {new_target.tag})。")
                                
                                # A-Move 整个小队到新目标
                                for unit in live_units_in_group:
                                    unit.attack(new_target)
                                
                                # 4. 更新总攻数据，防止每帧都切换目标
                                attack_data["target_tag"] = new_target.tag
                                attack_data["final_position"] = new_target.position
                            
                            else:
                                # 5. (备选) 如果视野里什么都看不到了
                                if live_units_in_group.idle.exists:
                                    final_target_pos = attack_data.get("final_position", self.enemy_start_locations[0])
                                    self.logger.info(f"总攻波次 {wave_id}: 目标丢失且无视野, 闲置单位 A-Move至最后已知位置。")
                                    for unit in live_units_in_group.idle:
                                        unit.attack(final_target_pos)
                        
                        # (已移除 Kiting 逻辑)
                        pass


                # --- 循环外：执行清理 (删除全灭的编队) ---
                for wave_id in waves_to_delete:
                    if wave_id in self.total_attack_groups:
                        del self.total_attack_groups[wave_id]

                # (已移除 Kiting 逻辑)
    # async def manage_attack(self):
    #     """
    #     【攻击总指挥】
    #     根据游戏情况决定并执行一种攻击策略。
    #     此函数在 on_step 中被调用, 且在 automatic_defense (自动防御) 之后。
    #     """
        
    #     # --- 1. (必须) 持续维护所有已发起的“总攻”编队 ---
    #     self.manage_total_attack_groups()


    #     # --- 2. (触发) 自动总攻决策 (硬编码逻辑) ---
        
    #     # (--- 新逻辑：计算“可用”兵力 ---)
        
    #     # 2.1 找出所有“已在总攻中”的单位
    #     busy_unit_tags = set()
    #     if self.total_attack_groups:
    #         # 遍历所有进行中的攻击波次
    #         for attack_data in self.total_attack_groups.values():
    #             # 将该波次中所有(存活)单位的 tag 添加到 "忙碌" 集合中
    #             busy_unit_tags.update(attack_data["unit_tags"])

    #     # 2.2 获取所有战斗单位
    #     all_combat_units = self._get_all_combat_units()
        
    #     # 2.3 筛选出“可用”的战斗单位 (不在忙碌集合中的)
    #     available_combat_units = all_combat_units.filter(
    #         lambda unit: unit.tag not in busy_unit_tags
    #     )
        
    #     # (--- 新逻辑结束 ---)
        

    #     # 检查：(已修改) “可用”兵力是否达到 5 个？
    #     # (原: len(combat_units) > 5)
    #     if len(available_combat_units) >= 3:
            
    #         # 兵力已到，开始寻找目标 (使用可用单位的中心点)
    #         target_to_attack = None
    #         units_center = available_combat_units.center

    #         # 优先级 1: 寻找已知的敌方“主基地”
    #         enemy_townhalls = self.enemy_structures.filter(
    #             lambda structure: structure.type_id in self.enemy_townhall_types
    #         )
            
    #         if enemy_townhalls.exists:
    #             target_to_attack = enemy_townhalls.closest_to(units_center)
            
    #         # 优先级 2: 如果没找到主基地，寻找任何“其他”敌方建筑
    #         elif self.enemy_structures.exists:
    #             target_to_attack = self.enemy_structures.closest_to(units_center)
            
    #         # 优先级 3: 如果连建筑都看不到...
    #         elif self.enemy_start_locations:
    #             # 备用方案A：如果此时看到了任何“单位”
    #             if self.enemy_units.exists:
    #                 target_to_attack = self.enemy_units.closest_to(units_center)
                
    #             # 备用方案B：(最终方案) 如果什么都看不到，就 A-Move 到敌人基地
    #             else:
    #                 target_position = self.enemy_start_locations[0]
    #                 # (已修改) 只 rally "可用" 单位
    #                 self.logger.info(f"自动总攻：可用兵力 {len(available_combat_units)}, 未发现敌人, 发起总攻至敌方出生点 {target_position}")
    #                 self.launch_total_attack(target_position, available_combat_units)
    #                 return # return 仍然是必要的，以防 Plan A 在同一帧被触发

    #         # 如果在前两步中找到了目标建筑/单位
    #         if target_to_attack:
    #             self.logger.info(f"自动总攻：可用兵力 {len(available_combat_units)}, 达到阈值 (5)。")
    #             self.logger.info(f"自动总攻：锁定目标 {target_to_attack.name} (Tag: {target_to_attack.tag})。")
                
    #             # 【!!! 发起总攻 !!!】
    #             # (已修改) 传入目标 和 “可用”单位
    #             self.launch_total_attack(target_to_attack, available_combat_units)
    #             return

    #     # --- 3. (其他) ... ---
    #     pass


    async def manage_attack(self):
        """
        【攻击总指挥】
        (V2: 仅维护总攻, 触发逻辑移至 execute_llm_attacks)
        
        根据游戏情况决定并执行一种攻击策略。
        此函数在 on_step 中被调用, 且在 automatic_defense (自动防御) 之后。
        """
        
        # --- 1. (必须) 持续维护所有已发起的“总攻”编队 ---
        # (此函数每帧运行，以更新现有攻击波次的状态,
        # 例如，在 GATHERING -> ATTACKING 切换, 或在目标丢失后自动寻找新目标)
        self.manage_total_attack_groups()

        # --- 2. (已移除) ---
        # (自动触发总攻的硬编码逻辑已被移除)
        # (现在，攻击的发起由 AdjestAgent 的输出
        #  并通过 run() -> execute_llm_attacks() 触发)
        
    async def _resolve_attack_targets(self, target_pos_data, target_unit_name) -> list:
            """
            [新] 解析 AdjestAgent 攻击指令的目标。
            返回一个 [target_object_1, target_object_2, ...] 列表 (Point2 或 Unit)
            """
            final_targets = []
            
            # 优先级 1: target_position
            if target_pos_data:
                try:
                    # 检查数据是 [x,y] 还是 [[x1,y1], [x2,y2]]
                    if isinstance(target_pos_data[0], list):
                        # 多个位置: [[x1,y1], [x2,y2]]
                        for pos in target_pos_data:
                            final_targets.append(Point2((pos[0], pos[1])))
                    elif len(target_pos_data) == 2 and isinstance(target_pos_data[0], int):
                        # 单个位置: [x,y]
                        final_targets.append(Point2((target_pos_data[0], target_pos_data[1])))
                    else:
                        self.logger.warning(f"无法解析 target_position 数据: {target_pos_data}")
                except Exception as e:
                    self.logger.error(f"解析 target_position 时出错: {e}. 数据: {target_pos_data}")
                    
            # 优先级 2: target_unit (if position was empty)
            elif target_unit_name:
                # 查找与 target_unit_name "最像" 的敌方单位
                all_enemies = self.enemy_units | self.enemy_structures
                if all_enemies.exists:
                    
                    target_unit_name_lower = target_unit_name.lower()
                    best_match_unit = None
                    
                    # 1. 尝试精确匹配
                    for unit in all_enemies:
                        if unit.name.lower() == target_unit_name_lower:
                            best_match_unit = unit
                            break
                            
                    # 2. 如果没有精确匹配, 尝试包含匹配
                    if not best_match_unit:
                        for unit in all_enemies:
                            if target_unit_name_lower in unit.name.lower():
                                best_match_unit = unit
                                break # 找到第一个包含的就用
                    
                    if best_match_unit:
                        self.logger.info(f"已解析 target_unit '{target_unit_name}' -> 敌方 {best_match_unit.name} (Tag: {best_match_unit.tag})")
                        final_targets.append(best_match_unit)
                    else:
                        self.logger.warning(f"在视野中未找到匹配 '{target_unit_name}' 的敌方单位。")
                        
            # --- [!! 修改后的备选方案逻辑 !!] ---
            # 备选方案: 如果未找到任何目标...
            if not final_targets:
                self.logger.warning(f"未解析到目标 '{target_pos_data}'/'{target_unit_name}'。 尝试寻找备选目标...")
                
                # 1. [新] 备选方案 A: 寻找最近的敌方基地
                # (使用 __init__ 中定义的 self.enemy_townhall_types)
                enemy_townhalls = self.enemy_structures.filter(
                    lambda structure: structure.type_id in self.enemy_townhall_types
                )
                
                if enemy_townhalls.exists:
                    # 找到离 *我方出生点* 最近的敌方基地
                    closest_enemy_townhall = enemy_townhalls.closest_to(self.start_location)
                    final_targets.append(closest_enemy_townhall)
                    self.logger.warning(f"备选方案A: 锁定最近的敌方基地 {closest_enemy_townhall.name} (Tag: {closest_enemy_townhall.tag})。")
                
                # 2. [原] 备选方案 B: 寻找敌方出生点
                elif self.enemy_start_locations:
                    final_targets.append(self.enemy_start_locations[0])
                    self.logger.warning(f"备选方案B: 未找到敌方基地, 默认攻击敌方出生点。")
                
                # 3. 最终失败
                else:
                    self.logger.error("无法解析任何目标, 且敌方出生点未知。")
            # --- [!! 修改结束 !!] ---

            return final_targets

    async def _launch_strike(self, unit_count: int, target_list: list, available_unit_tags: set):
            """
            [新] 启动一次小规模袭击 (Strike)。
            - available_unit_tags 是一个 set, 此函数将从中 *移除* 已分配的单位。
            [V2: 按实际兵力平分]
            """
            
            if not available_unit_tags:
                self.logger.warning("发起袭击失败: 没有可用的单位。")
                return

            if not target_list:
                self.logger.warning("发起袭击失败: 目标列表为空。")
                return

            num_targets = len(target_list)

            # --- 1. 选择兵力 (Selection) ---
            
            # 获取用于排序的"锚点"位置 (只使用第一个目标)
            if isinstance(target_list[0], Unit):
                anchor_pos = target_list[0].position
            else:
                anchor_pos = target_list[0] # It's a Point2

            # 获取所有可用的单位对象
            available_units = self.units.filter(lambda u: u.tag in available_unit_tags)
            
            if not available_units.exists:
                self.logger.warning("发起袭击失败: available_unit_tags 不为空, 但找不到单位对象。")
                return

            # 按照距离锚点的远近排序
            sorted_units = available_units.sorted(lambda u: u.distance_to(anchor_pos))
            
            # 选取指定数量的单位
            # (如果可用单位 < unit_count, units_to_assign 会包含所有可用单位)
            units_to_assign = sorted_units.take(unit_count, -1)
            
            if not units_to_assign.exists:
                self.logger.warning(f"发起袭击失败: 尝试选择 {unit_count} 个单位, 但没有单位被选中。")
                return

            self.logger.info(f"发起袭击 (Strike): 分配 {len(units_to_assign)} 个单位 (请求 {unit_count} 个) 到 {num_targets} 个目标。")

            assigned_unit_tags_this_strike = set()
            
            # --- 2. [!! 已修改 !!] 兵力计算 (Calculation) ---
            
            # [V2] 我们不再使用请求的 'unit_count'
            # 而是使用我们 *实际* 选中的单位数量 'len(units_to_assign)'
            actual_unit_count = len(units_to_assign)
            
            # (例如: 请求9个, 实际6个; 6 / 3 = 2)
            units_per_target = math.ceil(actual_unit_count / num_targets)
            
            if units_per_target == 0 and actual_unit_count > 0:
                # (安全检查, 防止 num_targets > actual_unit_count 导致 units_per_target 为 0)
                units_per_target = 1


            # --- 3. 分配任务 (Distribution) ---
            
            unit_index = 0
            units_list = list(units_to_assign) # 转换为列表以便切片

            for target in target_list:
                # (例如: 每次切片 2 个单位)
                units_for_this_target = units_list[unit_index : unit_index + units_per_target]
                
                if not units_for_this_target:
                    break # 没有更多单位了 (所有单位已分配完毕)

                target_pos_str = target.position.rounded if isinstance(target, Unit) else target.rounded
                
                # 命令单位攻击
                for unit in units_for_this_target:
                    unit.attack(target)
                    assigned_unit_tags_this_strike.add(unit.tag)
                
                self.logger.info(f"  - {len(units_for_this_target)} 个单位被派往 {target_pos_str}")
                unit_index += units_per_target

            # 4. (重要) 从本轮可用池中移除已分配的单位
            available_unit_tags.difference_update(assigned_unit_tags_this_strike)

    async def execute_llm_attacks(self, llm_attack_commands: list):
        """
        [新] 处理来自 AdjestAgent 的所有标准攻击指令。
        在 run() 方法中被调用。
        """
        self.logger.info(f"收到 {len(llm_attack_commands)} 条来自 AdjestAgent 的攻击指令。")
        
        # 1. 获取本轮所有可用的战斗单位
        # (即不在任何已发起的 "总攻" 编队中的单位)
        busy_unit_tags = set()
        if self.total_attack_groups:
            for attack_data in self.total_attack_groups.values():
                busy_unit_tags.update(attack_data["unit_tags"])
                
        # _get_all_combat_units() 是您在 llm_player.py 中的辅助函数
        available_combat_units = self._get_all_combat_units().filter(
            lambda unit: unit.tag not in busy_unit_tags
        )
        
        # (重要) 转换为 Tag 集合, 以便高效管理和防止重复分配
        available_unit_tags = {u.tag for u in available_combat_units} 

        for command in llm_attack_commands:
            # 如果本轮已没有可用单位, 停止处理后续攻击指令
            if not available_unit_tags:
                self.logger.warning("已无更多可用单位分配, 剩余的 LLM 攻击指令本轮将不执行。")
                break 

            unit_count = command.get("unit_count", 0)
            target_pos_data = command.get("target_position", [])
            target_unit_name = command.get("target_unit", "")
            
            # --- 2. 解析目标 ---
            target_list = await self._resolve_attack_targets(target_pos_data, target_unit_name)
            
            if not target_list:
                self.logger.warning(f"无法解析攻击指令的目标: {command}")
                continue # 跳过此指令
            
            # --- 3. 区分 "总攻" vs "袭击" ---
            
            if unit_count > 25:
                # --- 3.A. 总攻 (Total Attack) ---
                # 调集所有剩余的可用兵力
                units_to_assign_tags = set(available_unit_tags) # 复制集合
                units_to_assign = self.units.filter(lambda u: u.tag in units_to_assign_tags)
                
                if units_to_assign.exists:
                    # 总攻只支持一个主目标, 我们使用列表中的第一个
                    main_target = target_list[0]
                    target_str = main_target.position.rounded if isinstance(main_target, Unit) else main_target.rounded
                    
                    self.logger.info(f"发起总攻 (LLM请求 {unit_count}, 实际调集 {len(units_to_assign)}) -> {target_str}")
                    
                    # 使用现有的总攻逻辑
                    # (launch_total_attack 会创建编队, 由 manage_total_attack_groups 维护)
                    self.launch_total_attack(main_target, units_to_assign)
                    
                    # (重要) 消耗掉所有可用单位, 确保本轮不再分配
                    available_unit_tags.clear() 
                
            else:
                # --- 3.B. 袭击 (Strike) ---
                # 调集指定数量 (unit_count) 的兵力
                # (注意: _launch_strike 会修改 available_unit_tags 集合)
                await self._launch_strike(unit_count, target_list, available_unit_tags)

#### shy_end ####
    
    async def run(self, iteration: int):
        # send idle workers to minerals or gas automatically
        await self.distribute_workers()
        if self.config.own_race == "Terran":
            await self.set_terran_combat_rally_points()
            await self.automatic_defense()
            await self.manage_scouting()
            await self.manage_attack()

            # --- [新增] 驻防逻辑 ---
            # [修改] 每 224 帧（约10秒 @ 22.4 帧/秒）检查一次驻防
            garrison_check_interval = 224
            if iteration - self.last_garrison_check_time > garrison_check_interval:
                await self.manage_garrison()
                self.last_garrison_check_time = iteration
            # --- [新增] 驻防逻辑 结束 ---
            
        # for unit in self.units:
        #     if unit.type_id in [UnitTypeId.MULE] or unit.is_constructing_scv:
        #         continue
        #     enemies_in_range = self.enemy_units.in_attack_range_of(unit)
        #     if enemies_in_range.exists:
        #         target = self.get_lowest_health_enemy(enemies_in_range)
        #         if target:
        #             unit.attack(target)
        #     else:
        #         near_by_enemies = self.enemy_units.closer_than(self.scv_auto_attack_distance, unit.position)
        #         near_by_enemies = near_by_enemies.closer_than(self.scv_auto_attack_distance, self.start_location)
        #         target_enemy = self.get_lowest_health_enemy(near_by_enemies)
        #         if unit.type_id in [UnitTypeId.SCV] and self.time < self.scv_auto_attack_time and target_enemy:
        #             unit.attack(target_enemy)

        # 10 iteration -> 1.7s
        if self.config.enable_random_decision_interval:
            decision_iteration = random.randint(8, 12)
            decision_minerals = random.randint(130, 200)
        else:
            decision_iteration = 10
            decision_minerals = 170
        if (
            iteration % decision_iteration == 0
            and self.minerals >= decision_minerals
            or iteration == self.next_decision_time
        ):
            self.next_decision_time = iteration + 9 * decision_iteration

            self.log_current_iteration(iteration)

            obs_text = await self.obs_to_text()
            
            # RAG is not ready yet, so we skip it for now
            # if self.config.enable_rag:
            #     rag_summary, rag_think = self.rag_agent.run(obs_text)
            #     self.logging("rag_summary", rag_summary, save_trace=True)
            #     self.logging("rag_think", rag_think, save_trace=True, print_log=False)
            #     obs_text += "\n\n# Hint\n" + rag_summary



            if self.config.enable_plan or self.config.enable_plan_verifier:
                suggestions = self.get_suggestions()
                self.logging("suggestions", suggestions, save_trace=True, print_log=False)

                # 1. PlanAgent 运行
                plans, plan_think, plan_chat_history = self.plan_agent.run(obs_text, verifier=self.plan_verifier, suggestions=suggestions)
                self.logging("plans", plans, save_trace=True)
                self.logging("plan_think", plan_think, save_trace=True, print_log=False)
                self.logging("plan_chat_history", plan_chat_history, save_trace=True, print_log=False)

                # --- [!! 在这里添加修改 !!] ---
                # 2. AdjestAgent 运行 (默认调起)
                #    (我们使用 hasattr 检查以确保 adjest_agent 已被初始化)
                if hasattr(self, 'adjest_agent'): 

                    # 1. 获取本轮所有可用的战斗单位
                    # (即不在任何已发起的 "总攻" 编队中的单位)
                    busy_unit_tags = set()
                    if self.total_attack_groups:
                        for attack_data in self.total_attack_groups.values():
                            busy_unit_tags.update(attack_data["unit_tags"])
                            
                    # _get_all_combat_units() 是您在 llm_player.py 中的辅助函数
                    available_combat_units = self._get_all_combat_units().filter(
                        lambda unit: unit.tag not in busy_unit_tags
                    )
                    
                    # (重要) 转换为 Tag 集合, 以便高效管理和防止重复分配
                    available_unit_tags = {u.tag for u in available_combat_units} 

                    print(f"可用单位{len(available_unit_tags)}")

                    if len(available_unit_tags) > 5 and self.flag_test:
                        plans.append("Launch OFFENSE with 2 Marauder, 1 Marine, targeting OrbitalCommand")
                        self.flag_test = False
                    elif len(available_unit_tags) > 10:
                        plans.append("Launch OFFENSE with 10 Marauder, 16 Marine, targeting OrbitalCommand")
                        self.flag_test = True


                    # AdjestAgent 接收 plans 列表并进行分类
                    classified_results = self.adjest_agent.run(plans)
                    
                    # (重要) AdjestAgent 内部会保存累积的日志文件。
                    # 我们在这里 logging [当前轮次] 的结果
                    self.logging("classified_plan_results", classified_results, save_trace=True, print_log=False)
                    
                    # (可选) 额外记录标准攻击指令，以便在主日志中快速查看
                    standard_commands = classified_results.get("standard_attack_commands", [])
                    self.logging("standard_attack_commands_this_run", standard_commands, save_trace=True, print_log=True)

                    # --- [!! 新增的集成点 !!] ---
                    if standard_commands:
                        # 将 AdjestAgent 识别出的攻击指令
                        # 传递给新的攻击执行器
                        await self.execute_llm_attacks(standard_commands)
                    # --- [!! 新增结束 !!] ---
                        

                # --- [!! 修改结束 !!] ---

                # (重要) ActionAgent 仍然会运行
                # 但 base_player.py 中的 run_actions 已经阻止了 "ATTACK" 指令,
                # 所以这里的 actions 列表只包含 "Other Task" (如建造、训练)
                other_commands = classified_results.get("other_tasks", [])
                if other_commands:
                    actions, action_think, action_chat_history = self.action_agent.run(obs_text, other_commands, verifier=self.action_verifier)
                    self.logging("actions", actions, save_trace=True)
                    self.logging("action_think", action_think, save_trace=True, print_log=False)
                    self.logging("action_chat_history", action_chat_history, save_trace=True, print_log=False)
                else:
                    actions = []
            else:
                # ... (else 分支保持不变)
                actions, action_think, action_chat_history = self.agent.run(obs_text, verifier=self.action_verifier)
                # ...

            await self.run_actions(actions)
            
        elif iteration % 10 == 0:
            self.log_current_iteration(iteration)
