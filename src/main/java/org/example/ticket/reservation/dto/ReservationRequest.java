package org.example.ticket.reservation.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

import java.util.List;

public record ReservationRequest(
        @NotNull @Positive Long performanceTimeId,
        @NotEmpty List<@NotNull @Positive Long> seatIds
) {}
