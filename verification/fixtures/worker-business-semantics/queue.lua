local M = {}
local jobs = {}

local function active(job)
    return job and (job.state == "queued" or job.state == "running")
end

-- Driver callback, not bound to a supported test command.
function M.printer_media_lost(id)
    if active(jobs[id]) then jobs[id].state = "failed" end
end

-- Caller, binding and supported modes are outside the frozen material.
function M.printer_maintenance(mode)
    if mode == 7 then
        for _, job in pairs(jobs) do job.watching = nil end
    end
end

local function advance()
    for id = 1, 4 do
        local job = jobs[id]
        if active(job) then
            if job.cancel_requested then
                job.state = "cancelled"
                job.watching = nil
                job.fail_next = false
            else
                job.state = "running"
                if job.fail_next then
                    job.fail_next = false
                    job.state = "failed"
                else
                    job.printed = job.printed + 1
                    if job.watching then job.events = job.events + 1 end
                    if job.printed == job.pages then job.state = "done" end
                end
            end
        end
    end
end

local function request(command)
    local id, group, pages = command:match("^add (%d+) (%a) (%d+)$")
    if id then
        id, pages = tonumber(id), tonumber(pages)
        if id >= 1 and id <= 4 and pages >= 1 and pages <= 3 and not jobs[id] then
            jobs[id] = {pages=pages, printed=0, events=0, watching={}, group=group, state="queued"}
            return "CREATED"
        end
    end
    id = command:match("^query (%d+)$")
    if id and jobs[tonumber(id)] then
        local job = jobs[tonumber(id)]
        return string.format("%s printed=%d events=%d", job.state, job.printed, job.events)
    end
    id = command:match("^unwatch (%d+)$")
    if id and jobs[tonumber(id)] then
        jobs[tonumber(id)].watching = nil
        return "UNWATCHED"
    end
    group = command:match("^cancel (%a)$")
    if group then
        local selected = 0
        for _, job in pairs(jobs) do
            if active(job) and job.group == group then
                job.cancel_requested = true
                selected = selected + 1
            end
        end
        return "ACCEPTED " .. selected
    end
    id = command:match("^fail%-next (%d+)$")
    if id and jobs[tonumber(id)] then
        jobs[tonumber(id)].fail_next = true
        return "ARMED"
    end
    if command == "step" then advance(); return "ADVANCED" end
    if command == "reset" then jobs = {}; return "RESET" end
    return "INVALID"
end

-- The simulation host exposes only the documented request commands.
for _, command in ipairs(arg or {}) do print(request(command)) end
return M
