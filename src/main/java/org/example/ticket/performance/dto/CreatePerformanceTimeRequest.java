package org.example.ticket.performance.dto;
import jakarta.validation.constraints.*;
import java.time.LocalDateTime;
public record CreatePerformanceTimeRequest(@NotNull @Positive Long venueHallId,
                                           @NotNull LocalDateTime startsAt) {}
