#include <stdio.h>
#include <string.h>

typedef struct {
    int used, pages, printed, events, watching, cancel_requested, fail_next;
    char group;
    const char *state;
} Job;
static Job jobs[5];

static int active(const Job *j) {
    return j->used && (!strcmp(j->state, "queued") || !strcmp(j->state, "running"));
}

/* Driver callback, not bound to a supported test command. */
void printer_media_lost(int id) {
    Job *j = &jobs[id];
    if (active(j)) j->state = "failed";
}

/* Caller, binding and supported modes are outside the frozen material. */
void printer_maintenance(int mode) {
    if (mode == 7) {
        for (int id = 1; id <= 4; ++id) jobs[id].watching = 0;
    }
}

static void advance(void) {
    for (int id = 1; id <= 4; ++id) {
        Job *j = &jobs[id];
        if (!active(j)) continue;
        if (j->cancel_requested) {
            j->state = "cancelled";
            j->watching = 0;
            j->fail_next = 0;
            continue;
        }
        j->state = "running";
        if (j->fail_next) {
            j->fail_next = 0;
            j->state = "failed";
            continue;
        }
        ++j->printed;
        if (j->watching) ++j->events;
        if (j->printed == j->pages) j->state = "done";
    }
}

static void request(const char *command) {
    int id, pages;
    char group;
    if (sscanf(command, "add %d %c %d", &id, &group, &pages) == 3 &&
        id >= 1 && id <= 4 && pages >= 1 && pages <= 3 && !jobs[id].used) {
        jobs[id] = (Job){.used=1, .pages=pages, .watching=1, .group=group, .state="queued"};
        puts("CREATED");
    } else if (sscanf(command, "query %d", &id) == 1 && id >= 1 && id <= 4 && jobs[id].used) {
        printf("%s printed=%d events=%d\n", jobs[id].state, jobs[id].printed, jobs[id].events);
    } else if (sscanf(command, "unwatch %d", &id) == 1 && id >= 1 && id <= 4 && jobs[id].used) {
        jobs[id].watching = 0;
        puts("UNWATCHED");
    } else if (sscanf(command, "cancel %c", &group) == 1) {
        int selected = 0;
        for (id = 1; id <= 4; ++id) {
            if (active(&jobs[id]) && jobs[id].group == group) {
                jobs[id].cancel_requested = 1;
                ++selected;
            }
        }
        printf("ACCEPTED %d\n", selected);
    } else if (sscanf(command, "fail-next %d", &id) == 1 && id >= 1 && id <= 4 && jobs[id].used) {
        jobs[id].fail_next = 1;
        puts("ARMED");
    } else if (!strcmp(command, "step")) {
        advance();
        puts("ADVANCED");
    } else if (!strcmp(command, "reset")) {
        memset(jobs, 0, sizeof(jobs));
        puts("RESET");
    } else {
        puts("INVALID");
    }
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) request(argv[i]);
    return 0;
}
