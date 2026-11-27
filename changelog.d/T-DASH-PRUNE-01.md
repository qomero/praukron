- Dashboard: Work no longer carries the parts the lanes, force and 3D views
  replace. Removed: the "Complete dependency graph" drawing and its task
  dependency list, and the "Task graph" and "Dependency ordering" views under
  Views, with the drawing code, styles, embedded sources and translations only
  they used. The lanes, force and 3D views draw the dependency graph and its
  order, and the side panel opens every task. Work's Views tab (formerly
  "Dependency graph") keeps the critical path, phases, gates and calendar Gantt
  with their Mermaid sources; the state board, Review's decision inbox and
  Audit's task records are unchanged. `praukron graph` writes the same files as
  before, `task-graph.mmd` and `timeline.mmd` included. The rendered page is
  smaller by about 3 %.
