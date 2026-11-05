"""Set up the daily sync. An hourly check runs `sync --if-due`, so a missed or failed 8:00 AM sync catches up."""
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).parent.parent
TASK_NAME = "Job Tracker sync"


def task_xml(python, root):
    """A Windows Task Scheduler task. It also runs on battery, and pythonw keeps it from opening a window."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT1H</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python))}</Command>
      <Arguments>-m job_tracker sync --if-due</Arguments>
      <WorkingDirectory>{escape(str(root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install():
    python = Path(sys.executable)
    if sys.platform != "win32":
        print("Add this line to your crontab with `crontab -e`:")
        print(f'0 * * * * cd "{ROOT}" && "{python}" -m job_tracker sync --if-due')
        return 0
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "task.xml"
        file.write_text(task_xml(python.with_name("pythonw.exe"), ROOT), encoding="utf-16")
        done = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", str(file), "/f"])
    if done.returncode == 0:
        print(f'Scheduled. To turn it off, run: schtasks /delete /tn "{TASK_NAME}" /f')
    return done.returncode
