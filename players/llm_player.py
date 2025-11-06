from .base_player import BasePlayer
from agents import PlanAgent, ActionAgent, RagAgent, SingleAgent
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.ids.buff_id import BuffId
from sc2.ids.ability_id import AbilityId
from sc2.position import Point2

import random


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


    # --- 侦察逻辑 (V2: 主动检查视野) ---

    def _update_scouting_information(self):
        """
        [新] 主动检查视野, 记录新发现和丢失视野的单位。
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
                    
                    print(f"[侦察信息] {info}")
                    
                    # 添加到我们的信息字典中
                    self.scouting_information[current_time_int].append(info)
                    
                    # 额外记录到日志
                    self.logging("scout_discovery", info)

        # --- 2. 处理离开视野的单位 (用于调试打印) ---
        lost_tags = self.known_enemy_tags_in_vision - current_visible_tags
        
        for tag in lost_tags:
            # 从 BotAI 内部访问上一帧的单位地图
            last_known_unit = (
                self._enemy_units_previous_map.get(tag) or
                self._enemy_structures_previous_map.get(tag)
            )
            if last_known_unit:
                print(f"[侦察信息] 丢失 {last_known_unit.type_id.name} 的视野, 最后位置 {last_known_unit.position.rounded}")

        # --- 3. 更新状态 ---
        self.known_enemy_tags_in_vision = current_visible_tags


    async def manage_scouting(self):
        """
        管理侦察单位和信息收集。
        在每个 on_step 周期被 run 方法调用。
        """
        
        # [新] 首先, 更新我们的视野信息
        self._update_scouting_information()
        
        # 1. 检查当前侦察单位的状态
        if self.active_scout_unit_tag:
            scout_unit = self.units.find_by_tag(self.active_scout_unit_tag)
            
            # 侦察单位死亡或消失
            if not scout_unit: 
                print(f"侦察单位 (Tag: {self.active_scout_unit_tag}) 丢失 (判定为死亡).")
                self.active_scout_unit_tag = None
                self.scout_target_location = None
            
            # 侦察单位变为空闲 (可能已到达目的地, 或被手动拉回, 或卡住)
            elif scout_unit.is_idle: 
                print(f"侦察单位 {scout_unit.type_id.name} (Tag: {self.active_scout_unit_tag}) 已变为空闲.")
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
        target = self._get_scout_target()
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
                print("派遣 [SCV] (从工人中抽取)...") # <-- 修改了打印信息
                
                # 如果找到了合适的单位, 派遣它
                if scout_unit:
                    self._send_scout(scout_unit, target)

    def _get_scout_target(self):
        """
        决定下一个侦察目标点。
        优先级:
        1. 未侦察过的敌方出生点。
        2. 未侦察过的矿区 (优先去近的)。
        3. 已知的敌方基地 (如果所有点都侦察过了)。
        """
        
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
            # 返回距离我方基地最近的那个未侦察矿区
            return min(unscouted_expansions, key=lambda loc: loc.distance_to(self.start_location))
        
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

#### shy_end ####
    
    async def run(self, iteration: int):
        # send idle workers to minerals or gas automatically
        await self.distribute_workers()
        if self.config.own_race == "Terran":
            await self.set_terran_combat_rally_points()
            await self.automatic_defense()
            await self.manage_scouting()
            
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

                plans, plan_think, plan_chat_history = self.plan_agent.run(obs_text, verifier=self.plan_verifier, suggestions=suggestions)
                self.logging("plans", plans, save_trace=True)
                self.logging("plan_think", plan_think, save_trace=True, print_log=False)
                self.logging("plan_chat_history", plan_chat_history, save_trace=True, print_log=False)

                actions, action_think, action_chat_history = self.action_agent.run(obs_text, plans, verifier=self.action_verifier)
                self.logging("actions", actions, save_trace=True)
                self.logging("action_think", action_think, save_trace=True, print_log=False)
                self.logging("action_chat_history", action_chat_history, save_trace=True, print_log=False)
            else:
                actions, action_think, action_chat_history = self.agent.run(obs_text, verifier=self.action_verifier)
                self.logging("actions", actions, save_trace=True)
                self.logging("action_think", action_think, save_trace=True, print_log=False)
                self.logging("action_chat_history", action_chat_history, save_trace=True, print_log=False)

            await self.run_actions(actions)
            
        elif iteration % 10 == 0:
            self.log_current_iteration(iteration)
