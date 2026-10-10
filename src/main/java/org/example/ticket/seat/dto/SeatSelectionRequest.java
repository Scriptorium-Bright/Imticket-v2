package org.example.ticket.seat.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

import java.util.List;

public record SeatSelectionRequest(
        @NotNull @Positive Long performanceTimeId,
        @NotEmpty List<@NotNull @Positive Long> seatIds
) {}
