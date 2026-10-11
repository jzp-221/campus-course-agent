import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests import test_agent  # offline settings
from app.database import Base
from scripts.migrate_assignments_and_tasks import migrate_assignments_and_tasks, migration_structure


class LearningItemMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            connection.execute(text('PRAGMA foreign_keys=ON'))
            connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE coruses (id INTEGER PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE schedules (id INTEGER PRIMARY KEY, class_date DATE)'))
            connection.execute(text("INSERT INTO schedules VALUES (314, '2026-10-14')"))
            connection.execute(text('INSERT INTO users VALUES (1)'))
            connection.execute(text('INSERT INTO coruses VALUES (1)'))

    def test_check_does_not_create_tables(self):
        self.assertEqual(migration_structure(self.engine),{'assignments':None,'plan_tasks':None})
        self.assertFalse(inspect(self.engine).has_table('assignments'))

    def test_creates_only_two_tables_and_user_indexes(self):
        self.assertEqual(migrate_assignments_and_tasks(self.engine),['assignments','plan_tasks'])
        for name, structure in migration_structure(self.engine).items():
            self.assertTrue(any(index['column_names']==['user_id'] for index in structure['indexes']))
            self.assertEqual({fk['referred_table'] for fk in structure['foreign_keys']},{'users','coruses'})

    def test_second_run_is_noop_preserves_new_rows_and_legacy_schedule(self):
        migrate_assignments_and_tasks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO plan_tasks
                (id,user_id,title,create_time,update_time) VALUES
                (1,1,'保留任务','2026-10-11 12:00:00','2026-10-11 12:00:00')"""))
            before = connection.execute(text('SELECT * FROM plan_tasks')).all()
        self.assertEqual(migrate_assignments_and_tasks(self.engine),[])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT * FROM plan_tasks')).all(),before)
            self.assertEqual(connection.execute(text('SELECT * FROM schedules')).one(),(314,'2026-10-14'))

    def test_partial_migration_resumes_missing_table(self):
        migrate_assignments_and_tasks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text('DROP TABLE plan_tasks'))
        self.assertEqual(migrate_assignments_and_tasks(self.engine),['plan_tasks'])

    def test_missing_user_index_is_repaired_without_recreating_table(self):
        migrate_assignments_and_tasks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text('DROP INDEX ix_assignments_user_id'))
        self.assertEqual(migrate_assignments_and_tasks(self.engine),['ix_assignments_user_id'])

    def test_timestamps_have_no_server_default_and_are_required(self):
        migrate_assignments_and_tasks(self.engine)
        for structure in migration_structure(self.engine).values():
            for column in structure['columns']:
                if column['name'] in ('create_time','update_time'):
                    self.assertFalse(column['nullable'])
                    self.assertIsNone(column['default'])
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(text("INSERT INTO plan_tasks (user_id,title) VALUES (1,'无时间戳')"))

    def test_database_defaults_and_foreign_keys_are_enforced(self):
        migrate_assignments_and_tasks(self.engine)
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO plan_tasks
                (user_id,title,create_time,update_time) VALUES
                (1,'默认值','2026-10-11 12:00:00','2026-10-11 12:00:00')"""))
            self.assertEqual(connection.execute(text('SELECT status,defer_count FROM plan_tasks')).one(),('pending',0))
        with self.assertRaises(IntegrityError), self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO plan_tasks
                (user_id,course_id,title,create_time,update_time) VALUES
                (1,999,'不存在的课程','2026-10-11 12:00:00','2026-10-11 12:00:00')"""))

    def test_missing_parent_fails_without_creating_new_tables(self):
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with self.assertRaisesRegex(RuntimeError,'users 表不存在'):
            migrate_assignments_and_tasks(engine)

    def test_existing_incompatible_table_fails_without_dropping_data(self):
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE assignments (id INTEGER PRIMARY KEY)'))
            connection.execute(text('INSERT INTO assignments VALUES (99)'))
        with self.assertRaisesRegex(RuntimeError,'字段不符合模型'):
            migrate_assignments_and_tasks(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text('SELECT * FROM assignments')).one(),(99,))


if __name__ == '__main__':
    unittest.main()
