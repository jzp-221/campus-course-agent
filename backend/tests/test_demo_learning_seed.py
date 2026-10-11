from datetime import date, timedelta
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tests import test_agent
from app.database import Base
from app.models.assignment import Assignment
from app.models.plan_task import PlanTask
from app.models.coures import Course
from app.models.user import User
from app.models.schedule import Schedule
from app.models.study_plan import StudyPlan
from tests.manual.seed_demo_assignments import (
    ASSIGNMENT_TEMPLATES, EXAM_TEMPLATES, build_demo, clear_demo, sync_demo,
)


class DemoLearningSeedTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026,10,11)
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        event.listen(self.engine,'connect',lambda connection,_:connection.execute('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine,expire_on_commit=False)
        names = sorted({name for name,_,_ in ASSIGNMENT_TEMPLATES+EXAM_TEMPLATES})
        with self.sessions() as db:
            self.students = [User(username=name,name=name,password='offline',role='student') for name in ('aaa','bbb')]
            self.courses = [Course(name=name,code=f'C{index}') for index,name in enumerate(names)]
            db.add_all(self.students+self.courses)
            db.commit()
        self.wanted = build_demo(self.students,self.courses,self.today)

    def test_demo_counts_and_sparse_student_are_distinct(self):
        aaa,bbb = (student.id for student in self.students)
        rows = self.wanted['assignments']
        self.assertEqual(sum(row['user_id']==aaa and row['type']=='assignment' for row in rows),9)
        self.assertEqual(sum(row['user_id']==aaa and row['type']=='exam' for row in rows),3)
        self.assertEqual(sum(row['user_id']==bbb for row in rows),3)
        self.assertEqual(len(self.wanted['plan_tasks']),5)
        aaa_rows = [row for row in rows if row['user_id'] == aaa]
        with self.subTest(state='done'):
            done = [row for row in aaa_rows if row['status'] == 'done']
            self.assertEqual(len(done), 1)
            self.assertEqual(done[0]['due_at'].date(), self.today-timedelta(days=2))
        with self.subTest(state='overdue'):
            self.assertTrue(any(row['status']=='pending' and row['due_at'].date()<self.today for row in aaa_rows))
        with self.subTest(state='soon'):
            self.assertTrue(any(row['status']=='pending' and self.today<=row['due_at'].date()<=self.today+timedelta(days=3) for row in aaa_rows))

    def test_deadlines_are_relative_except_explicit_holiday_example(self):
        for row in self.wanted['assignments']:
            if '-holiday]' in row['note']:
                self.assertEqual(row['due_at'].date(),date(2027,1,1))
            elif 'aaa-a01]' in row['note']:
                self.assertEqual(row['due_at'].date(),self.today-timedelta(days=2))
                self.assertEqual(row['status'],'done')
            elif 'aaa-a02]' in row['note']:
                self.assertEqual(row['due_at'].date(),self.today-timedelta(days=1))
                self.assertEqual(row['status'],'pending')
            else:
                self.assertTrue(self.today+timedelta(days=1)<=row['due_at'].date()<=self.today+timedelta(days=21))
            self.assertIsNone(row['due_at'].tzinfo)

    def test_relative_deadlines_move_when_run_date_changes(self):
        later = build_demo(self.students,self.courses,self.today+timedelta(days=10))
        for old,new in zip(self.wanted['assignments'],later['assignments']):
            if '-holiday]' not in old['note']:
                self.assertEqual(new['due_at']-old['due_at'],timedelta(days=10))

    def test_one_done_task_and_one_twice_deferred_task(self):
        rows = self.wanted['plan_tasks']
        self.assertEqual(sum(row['status']=='done' for row in rows),1)
        self.assertEqual(sum(row['defer_count']==2 for row in rows),1)

    def test_repeat_seed_keeps_ids_and_does_not_duplicate(self):
        with self.sessions() as db, db.begin():
            self.assertEqual(sync_demo(db,self.students,self.wanted)['created'],20)
        with self.sessions() as db, db.begin():
            before = [row.id for row in db.query(Assignment).order_by(Assignment.id)]
            stats = sync_demo(db,self.students,self.wanted)
            self.assertEqual(stats,{'created':0,'updated':0,'unchanged':20})
            self.assertEqual([row.id for row in db.query(Assignment).order_by(Assignment.id)],before)

    def test_clean_only_own_marker_preserves_non_demo_and_original_assets(self):
        with self.sessions() as db, db.begin():
            sync_demo(db,self.students,self.wanted)
            owner = self.students[0].id
            course = self.courses[0].id
            db.add_all([Assignment(user_id=owner,course_id=course,type='assignment',title='真实作业',due_at=self.wanted['assignments'][0]['due_at']),
                        PlanTask(user_id=owner,title='真实任务'),
                        Schedule(user_id=owner,course_id=course,class_date=self.today),
                        StudyPlan(user_id=owner,content='原文本计划')])
        with self.sessions() as db, db.begin():
            self.assertEqual(clear_demo(db,self.students),{'assignments':15,'plan_tasks':5})
            self.assertEqual(db.query(Assignment).one().title,'真实作业')
            self.assertEqual(db.query(PlanTask).one().title,'真实任务')
            self.assertEqual(db.query(Schedule).count(),1)
            self.assertEqual(db.query(StudyPlan).count(),1)
            self.assertEqual(db.query(Course).count(),len(self.courses))
            self.assertEqual(clear_demo(db,self.students),{'assignments':0,'plan_tasks':0})

    def test_whole_seed_rolls_back_on_failure(self):
        with self.assertRaises(RuntimeError):
            with self.sessions() as db, db.begin():
                sync_demo(db,self.students,self.wanted)
                raise RuntimeError('模拟后续操作失败')
        with self.sessions() as db:
            self.assertEqual(db.query(Assignment).count(),0)
            self.assertEqual(db.query(PlanTask).count(),0)

    def test_missing_real_course_or_student_is_rejected(self):
        from app.common.exceptions import BusinessException
        with self.assertRaisesRegex(BusinessException,'缺少现有演示课程'):
            build_demo(self.students,self.courses[:-1],self.today)
        with self.assertRaisesRegex(BusinessException,'请先创建'):
            build_demo(self.students[:1],self.courses,self.today)


if __name__ == '__main__':
    unittest.main()
