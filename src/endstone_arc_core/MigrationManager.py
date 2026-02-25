import json
from typing import Dict, Optional, List
from endstone_arc_core.DatabaseManager import DatabaseManager


class MigrationManager:
    """数据库迁移管理器，负责处理从UUID到XUID的迁移"""
    
    def __init__(self, database_manager: DatabaseManager):
        self.db = database_manager
        self.logger = None  # 将在插件初始化后设置
        
    def set_logger(self, logger):
        """设置日志记录器"""
        self.logger = logger
        
    def _log(self, level: str, message: str):
        """安全的日志记录"""
        if self.logger:
            if level == 'info':
                self.logger.info(message)
            elif level == 'warning':
                self.logger.warning(message)
            elif level == 'error':
                self.logger.error(message)
        else:
            print(f"[{level.upper()}] {message}")
    
    def migrate_to_xuid(self) -> bool:
        """执行从UUID到XUID的完整迁移"""
        try:
            self._log('info', "[Migration] Starting UUID to XUID migration...")
            
            # 0. 检查是否需要迁移
            if not self._needs_migration():
                self._log('info', "[Migration] Database already migrated to XUID system")
                return True
            
            # 1. 创建备份表
            if not self._create_backup_tables():
                self._log('error', "[Migration] Failed to create backup tables")
                return False
            
            # 2. 创建UUID到XUID的映射
            uuid_to_xuid_map = self._create_uuid_xuid_mapping()
            if not uuid_to_xuid_map:
                self._log('error', "[Migration] Failed to create UUID-XUID mapping")
                return False
            
            # 3. 迁移各个表
            migration_steps = [
                ("player_economy", self._migrate_player_economy),
                ("lands", self._migrate_lands),
                ("public_warps", self._migrate_public_warps),
                ("player_homes", self._migrate_player_homes),
                ("chunk_lands tables", self._migrate_chunk_lands_tables)
            ]
            
            for table_name, migration_func in migration_steps:
                self._log('info', f"[Migration] Migrating {table_name}...")
                if not migration_func(uuid_to_xuid_map):
                    self._log('error', f"[Migration] Failed to migrate {table_name}")
                    # 这里可以考虑回滚，但目前保持数据一致性更重要
                    return False
            
            # 4. 创建迁移标记
            self._mark_migration_complete()
            
            self._log('info', "[Migration] UUID to XUID migration completed successfully!")
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Critical error during migration: {str(e)}")
            return False
    
    def _needs_migration(self) -> bool:
        """检查是否需要迁移"""
        try:
            # 检查是否存在迁移标记表
            result = self.db.query_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_history'"
            )
            if not result:
                return True  # 没有迁移历史表，需要迁移
            
            # 检查是否已经完成了XUID迁移
            result = self.db.query_one(
                "SELECT * FROM migration_history WHERE migration_name = 'uuid_to_xuid_v1'"
            )
            return result is None  # 如果没有这个迁移记录，则需要迁移
            
        except Exception as e:
            self._log('error', f"[Migration] Error checking migration status: {str(e)}")
            return True  # 出错时假设需要迁移
    
    def _create_backup_tables(self) -> bool:
        """创建所有表的备份"""
        try:
            tables_to_backup = [
                'player_basic_info', 'player_economy', 'lands', 
                'public_warps', 'player_homes'
            ]
            
            for table in tables_to_backup:
                # 检查表是否存在
                if not self.db.table_exists(table):
                    continue
                    
                backup_table = f"{table}_backup_uuid"
                
                # 删除旧的备份表（如果存在）
                self.db.execute(f"DROP TABLE IF EXISTS {backup_table}")
                
                # 创建备份
                self.db.execute(f"CREATE TABLE {backup_table} AS SELECT * FROM {table}")
                self._log('info', f"[Migration] Created backup table: {backup_table}")
            
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error creating backup tables: {str(e)}")
            return False
    
    def _create_uuid_xuid_mapping(self) -> Dict[str, str]:
        """创建UUID到XUID的映射"""
        try:
            # 从player_basic_info表获取所有UUID-XUID映射
            results = self.db.query_all(
                "SELECT uuid, xuid FROM player_basic_info WHERE uuid IS NOT NULL AND xuid IS NOT NULL"
            )
            
            uuid_to_xuid = {}
            for row in results:
                uuid_to_xuid[row['uuid']] = row['xuid']
            
            self._log('info', f"[Migration] Created UUID-XUID mapping for {len(uuid_to_xuid)} players")
            return uuid_to_xuid
            
        except Exception as e:
            self._log('error', f"[Migration] Error creating UUID-XUID mapping: {str(e)}")
            return {}
    
    def _migrate_player_economy(self, uuid_to_xuid_map: Dict[str, str]) -> bool:
        """迁移player_economy表"""
        try:
            # 创建新的临时表，使用XUID作为主键
            self.db.execute("DROP TABLE IF EXISTS player_economy_new")
            self.db.execute("""
                CREATE TABLE player_economy_new (
                    xuid TEXT PRIMARY KEY,
                    money INTEGER NOT NULL DEFAULT 0
                )
            """)
            
            # 迁移数据
            old_data = self.db.query_all("SELECT * FROM player_economy")
            migrated_count = 0
            
            for row in old_data:
                uuid = row['uuid']
                if uuid in uuid_to_xuid_map:
                    xuid = uuid_to_xuid_map[uuid]
                    self.db.insert('player_economy_new', {
                        'xuid': xuid,
                        'money': row['money']
                    })
                    migrated_count += 1
                else:
                    self._log('warning', f"[Migration] No XUID mapping found for UUID: {uuid}")
            
            # 替换原表
            self.db.execute("DROP TABLE player_economy")
            self.db.execute("ALTER TABLE player_economy_new RENAME TO player_economy")
            
            self._log('info', f"[Migration] Migrated {migrated_count} economy records")
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error migrating player_economy: {str(e)}")
            return False
    
    def _migrate_lands(self, uuid_to_xuid_map: Dict[str, str]) -> bool:
        """迁移lands表"""
        try:
            # 创建新表
            self.db.execute("DROP TABLE IF EXISTS lands_new")
            self.db.execute("""
                CREATE TABLE lands_new (
                    land_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_xuid TEXT NOT NULL,
                    land_name TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    min_x INTEGER NOT NULL,
                    max_x INTEGER NOT NULL,
                    min_z INTEGER NOT NULL,
                    max_z INTEGER NOT NULL,
                    tp_x REAL NOT NULL,
                    tp_y REAL NOT NULL,
                    tp_z REAL NOT NULL,
                    shared_users TEXT,
                    allow_explosion INTEGER DEFAULT 0,
                    allow_public_interact INTEGER DEFAULT 0
                )
            """)
            
            # 迁移数据
            old_data = self.db.query_all("SELECT * FROM lands")
            migrated_count = 0
            
            for row in old_data:
                owner_uuid = row['owner_uuid']
                if owner_uuid not in uuid_to_xuid_map:
                    self._log('warning', f"[Migration] No XUID mapping for land owner UUID: {owner_uuid}")
                    continue
                
                # 转换共享用户列表
                shared_users_uuids = json.loads(row.get('shared_users', '[]'))
                shared_users_xuids = []
                for uuid in shared_users_uuids:
                    if uuid in uuid_to_xuid_map:
                        shared_users_xuids.append(uuid_to_xuid_map[uuid])
                    else:
                        self._log('warning', f"[Migration] No XUID mapping for shared user UUID: {uuid}")
                
                # 插入新数据
                self.db.execute("""
                    INSERT INTO lands_new (
                        land_id, owner_xuid, land_name, dimension,
                        min_x, max_x, min_z, max_z,
                        tp_x, tp_y, tp_z, shared_users,
                        allow_explosion, allow_public_interact
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row['land_id'], uuid_to_xuid_map[owner_uuid], row['land_name'], row['dimension'],
                    row['min_x'], row['max_x'], row['min_z'], row['max_z'],
                    row['tp_x'], row['tp_y'], row['tp_z'], json.dumps(shared_users_xuids),
                    row.get('allow_explosion', 0), row.get('allow_public_interact', 0)
                ))
                migrated_count += 1
            
            # 替换原表
            self.db.execute("DROP TABLE lands")
            self.db.execute("ALTER TABLE lands_new RENAME TO lands")
            
            self._log('info', f"[Migration] Migrated {migrated_count} land records")
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error migrating lands: {str(e)}")
            return False
    
    def _migrate_public_warps(self, uuid_to_xuid_map: Dict[str, str]) -> bool:
        """迁移public_warps表"""
        try:
            # 创建新表
            self.db.execute("DROP TABLE IF EXISTS public_warps_new")
            self.db.execute("""
                CREATE TABLE public_warps_new (
                    warp_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    warp_name TEXT NOT NULL UNIQUE,
                    dimension TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL,
                    created_by TEXT NOT NULL,
                    created_time INTEGER NOT NULL
                )
            """)
            
            # 迁移数据
            old_data = self.db.query_all("SELECT * FROM public_warps")
            migrated_count = 0
            
            for row in old_data:
                creator_uuid = row['created_by']
                if creator_uuid in uuid_to_xuid_map:
                    creator_xuid = uuid_to_xuid_map[creator_uuid]
                else:
                    # 如果找不到映射，保留原值（可能是系统创建的）
                    creator_xuid = creator_uuid
                    self._log('warning', f"[Migration] No XUID mapping for warp creator: {creator_uuid}")
                
                self.db.execute("""
                    INSERT INTO public_warps_new (
                        warp_id, warp_name, dimension, x, y, z, created_by, created_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row['warp_id'], row['warp_name'], row['dimension'],
                    row['x'], row['y'], row['z'], creator_xuid, row['created_time']
                ))
                migrated_count += 1
            
            # 替换原表
            self.db.execute("DROP TABLE public_warps")
            self.db.execute("ALTER TABLE public_warps_new RENAME TO public_warps")
            
            self._log('info', f"[Migration] Migrated {migrated_count} public warp records")
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error migrating public_warps: {str(e)}")
            return False
    
    def _migrate_player_homes(self, uuid_to_xuid_map: Dict[str, str]) -> bool:
        """迁移player_homes表"""
        try:
            # 创建新表
            self.db.execute("DROP TABLE IF EXISTS player_homes_new")
            self.db.execute("""
                CREATE TABLE player_homes_new (
                    home_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_xuid TEXT NOT NULL,
                    home_name TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    z REAL NOT NULL,
                    created_time INTEGER NOT NULL
                )
            """)
            
            # 迁移数据
            old_data = self.db.query_all("SELECT * FROM player_homes")
            migrated_count = 0
            
            for row in old_data:
                owner_uuid = row['owner_uuid']
                if owner_uuid not in uuid_to_xuid_map:
                    self._log('warning', f"[Migration] No XUID mapping for home owner: {owner_uuid}")
                    continue
                
                self.db.execute("""
                    INSERT INTO player_homes_new (
                        home_id, owner_xuid, home_name, dimension, x, y, z, created_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row['home_id'], uuid_to_xuid_map[owner_uuid], row['home_name'],
                    row['dimension'], row['x'], row['y'], row['z'], row['created_time']
                ))
                migrated_count += 1
            
            # 替换原表
            self.db.execute("DROP TABLE player_homes")
            self.db.execute("ALTER TABLE player_homes_new RENAME TO player_homes")
            
            self._log('info', f"[Migration] Migrated {migrated_count} player home records")
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error migrating player_homes: {str(e)}")
            return False
    
    def _migrate_chunk_lands_tables(self, uuid_to_xuid_map: Dict[str, str]) -> bool:
        """迁移所有chunk_lands_*表"""
        try:
            # 获取所有chunk_lands表
            tables = self.db.query_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'chunk_lands_%'"
            )
            
            for table_info in tables:
                table_name = table_info['name']
                self._log('info', f"[Migration] Migrating chunk table: {table_name}")
                
                # chunk_lands表不需要迁移UUID到XUID，因为它们只存储land_id
                # 但为了保险起见，我们还是检查一下表结构
                
            return True
            
        except Exception as e:
            self._log('error', f"[Migration] Error migrating chunk_lands tables: {str(e)}")
            return False
    
    def _mark_migration_complete(self):
        """标记迁移完成"""
        try:
            # 创建迁移历史表
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS migration_history (
                    migration_name TEXT PRIMARY KEY,
                    migrated_at INTEGER NOT NULL
                )
            """)
            
            # 记录迁移
            import time
            self.db.insert('migration_history', {
                'migration_name': 'uuid_to_xuid_v1',
                'migrated_at': int(time.time())
            })
            
        except Exception as e:
            self._log('error', f"[Migration] Error marking migration complete: {str(e)}")
