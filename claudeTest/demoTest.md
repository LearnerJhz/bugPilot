cd /Users/bytedance/person/githubBug/bugPilot
python3 -m cli run <任务名> \
  --description "<把 bug 说清楚：现象 + 怎么复现 + 期望>" \
  --repo <要修的目标仓库绝对路径>



用一个冒泡程序举例子：  
cd /Users/bytedance/person/githubBug/bugpilot-demo-target && git checkout -q buggy.py && cd /Users/bytedance/person/githubBug/bugPilot && python3 -m cli run demo_sort --description "test_algo.py 失败：bubble_sort 结果是降序，应为升序。请定位 buggy.py 里的根因并修复，使 test_algo.py 全部用例通过。" --repo /Users/bytedance/person/githubBug/bugpilot-demo-target
