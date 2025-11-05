from sc2.bot_ai import BotAI
from sc2.constants import UnitTypeId
from sc2.ids.ability_id import AbilityId
from sc2.units import Units
from sc2.unit import Unit
import time

class MinerPlayer(BotAI):
    def __init__(self):
        super().__init__()
        
        self.race = "Protoss"
        
        race_data = {
            "Terran": {
                "gas_structure": UnitTypeId.REFINERY,
                "base_structure": UnitTypeId.COMMANDCENTER,
                "worker_unit": UnitTypeId.SCV,
                "supply_structure": UnitTypeId.SUPPLYDEPOT,
            },
            "Protoss": {
                "gas_structure": UnitTypeId.ASSIMILATOR,
                "base_structure": UnitTypeId.NEXUS,
                "worker_unit": UnitTypeId.PROBE,
                "supply_structure": UnitTypeId.PYLON,
            },
            "Zerg": {
                "gas_structure": UnitTypeId.EXTRACTOR,
                "base_structure": UnitTypeId.HATCHERY,
                "worker_unit": UnitTypeId.DRONE,
                "supply_structure": UnitTypeId.OVERLORD,
            },
        }

        if self.race not in race_data:
            raise ValueError(f"Unknown race: {self.race}")

        for key, value in race_data[self.race].items():
            setattr(self, key, value)
        
    async def on_step(self, iteration):
        if iteration % 10 == 0:
            await self.distribute_workers()
        await self.build_supply()
        await self.build_refinery()
        await self.build_workers()
        await self.expand()
    
    async def build_refinery(self):
        n_refinery = len(self.structures(self.base_structure))
        if n_refinery < 2 and self.can_afford(self.gas_structure):
            cloest_gases = self.vespene_geyser.closest_n_units(self.start_location, 100)
            cloest_scv = self.workers.closest_to(cloest_gases[n_refinery])
            cloest_scv.build(self.gas_structure, position=cloest_gases[n_refinery])

    async def build_workers(self):
        for cc in self.townhalls(self.base_structure).ready.idle:
            if self.can_afford(self.worker_unit):
                cc.train(self.worker_unit)

    async def expand(self):
        if self.townhalls(self.base_structure).amount < 3 and self.can_afford(self.base_structure):
            await self.expand_now()

    async def build_supply(self):
        ccs = self.townhalls(self.base_structure).ready
        if ccs.exists:
            cc = ccs.first
            if self.supply_left < 4 and not self.already_pending(self.supply_structure):
                if self.can_afford(self.supply_structure):
                    await self.build(self.supply_structure, near=cc.position.towards(self.game_info.map_center, 5))

    async def distribute_workers(self, resource_ratio: float = 2.0) -> None:
        """
        根据全局矿气比分配工人，优先将工人派往采集gas。
        会将gas_site附近采集mineral的worker调往gas_site以最大化其利用。
        """
        # 必要性检查
        if not self.townhalls.ready or not self.workers:
            return

        idle_workers = self.workers.idle
        
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

        # 2. 统计每个点的缺工数
        gas_tasks = {}
        mineral_tasks = {}
        
        # 气矿：ideal=3，surplus_harvesters<0 时表示缺工
        for g in gas_refineries:
            missing = max(0, -g.surplus_harvesters)
            if missing:
                gas_tasks[g] = missing
        
        # 矿点：每个矿最多2个工人
        for m in mineral_patches:
            need = max(0, 2 - m.assigned_harvesters)
            if need:
                mineral_tasks[m] = need

        # 3. 优先处理gas_sites - 从附近mineral_sites调worker + idle_workers
        available_idle_workers = list(idle_workers)
        
        for gas_site in list(gas_tasks.keys()):
            needed = gas_tasks[gas_site]
            if needed <= 0:
                continue
                
            # 找到这个gas_site附近正在采集mineral的workers
            nearby_mineral_workers = []
            
            for mineral in mineral_patches:
                # 只考虑距离gas_site较近的mineral
                if mineral.distance_to(gas_site) < 10:  # 距离阈值可调整
                    # 找到正在采集这个mineral的workers
                    for worker in self.workers.gathering:
                        # 通过距离判断worker是否在采集这个mineral
                        if worker.distance_to(mineral) < 2:
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

        # 4. 用剩余的idle_workers填补mineral_sites
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

        # 5. 如果还有gas_site缺工且还有mineral workers可调配，进行第二轮调配
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
                            if worker.distance_to(mineral) < 2:
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
